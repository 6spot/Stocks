"""Committed-visible change watermarks and resumable incremental scans.

The contract is subtle and worth stating plainly, because the obvious
implementations are all wrong:

* a timestamp column is wrong, because a transaction that starts at 07:59 and
  commits at 08:01 carries a 07:59 timestamp, so a reader at 08:00 would later
  treat it as already scanned;
* a sequence allocated at INSERT time is wrong for the same reason: the value is
  handed out before the row is visible, so a gap can appear behind a committed
  watermark;
* `collected_at` or `published_at` is wrong because they describe the content, not
  when it became researchable.

What works, and what this module implements, is a counter row whose increment is
locked until commit. Writers serialize on it, so the commit order equals the
watermark order. A reader that obtains watermark ``N`` therefore knows every record
with a lower watermark is already visible — there is no hole behind ``N``.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from stocks.models import ChangeCounter, ChangeRecord, Document, Fact, ResearchCursor

COUNTER_NAME = "research_change"
CHANGE_KINDS = (
    "document_ingested",
    "document_parsed",
    "document_classified",
    "document_indexed",
    "fact_mapped",
    "fact_extracted",
)
CHANGE_SCAN_PROGRESS_VERSION = 1


class ChangeCursorError(RuntimeError):
    """A window, cursor or scan continuation could not be established."""

    def __init__(self, code: str, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.detail}


def ensure_counter(session: Session) -> None:
    """Create the counter row if a fresh database has none, without committing.

    Uses ``ON CONFLICT DO NOTHING`` rather than a read-then-insert: two callers in
    different transactions can both find it missing, and a plain insert would make
    one of them fail on the primary key instead of simply observing the row.

    Returns nothing on purpose. Handing back the ORM row would let a caller hold an
    entity bound to a Session that is about to close, and the watermark must always
    be read fresh from the database anyway.
    """

    session.execute(
        insert(ChangeCounter)
        .values(name=COUNTER_NAME, watermark=0)
        .on_conflict_do_nothing(index_elements=["name"])
    )


def record_change(
    session: Session,
    *,
    kind: str,
    document_id: UUID,
    fact_id: UUID | None = None,
    recorded_at: datetime,
) -> int:
    """Allocate the next committed-visible watermark and write one change record.

    Must run inside the same transaction as the change it describes. The counter
    increment takes a row lock that is held until that transaction commits, which
    is exactly what makes the watermark ordering equal the commit ordering.
    Callers never commit here: the surrounding transaction owns the boundary.
    """

    if kind not in CHANGE_KINDS:
        raise ValueError(f"unknown change kind: {kind}")
    if recorded_at.utcoffset() is None:
        raise ValueError("recorded_at must include a timezone")
    if kind.startswith("fact_") and fact_id is None:
        raise ValueError("a fact change requires its fact id")

    ensure_counter(session)
    # The UPDATE both takes the row lock and returns the value it produced, so two
    # writers in either order cannot observe the same watermark. The lock is held
    # until the caller's transaction commits, which is what makes the ordering
    # equal to commit order.
    result = session.execute(
        update(ChangeCounter)
        .where(ChangeCounter.name == COUNTER_NAME)
        .values(watermark=ChangeCounter.watermark + 1)
        .returning(ChangeCounter.watermark)
    )
    watermark = result.scalar_one()
    session.add(
        ChangeRecord(
            watermark=watermark,
            kind=kind,
            document_id=document_id,
            fact_id=fact_id,
            recorded_at=recorded_at,
        )
    )
    session.flush()
    return watermark


def current_watermark(session: Session) -> int:
    """The highest watermark that is definitely committed.

    Reading the counter in its own transaction is enough: because writers hold the
    row lock until commit, a value this transaction can see has no uncommitted
    writer behind it.
    """

    value = session.scalar(
        select(ChangeCounter.watermark).where(ChangeCounter.name == COUNTER_NAME)
    )
    return int(value or 0)


def scope_fingerprint(task_id: str, scope: dict) -> str:
    """Deterministic identity of one Task + research scope.

    Two Tasks, or one Task with a changed scope, must not share progress, so the
    fingerprint includes both. Key order does not matter.
    """

    if not isinstance(task_id, str) or not task_id.strip():
        raise ChangeCursorError(
            "SCOPE_INVALID", "A research cursor requires a task id"
        )
    if not isinstance(scope, dict):
        raise ChangeCursorError(
            "SCOPE_INVALID", "A research scope must be a mapping"
        )
    payload = json.dumps(
        {"task_id": task_id, "scope": scope},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChangeWindow:
    """The half-open watermark range one incremental scan covers."""

    start: int
    end: int
    task_id: str
    fingerprint: str
    resume: bool

    def to_dict(self) -> dict:
        return {
            "change_watermark_start": self.start,
            "change_watermark_end": self.end,
            "scope_fingerprint": self.fingerprint,
            "resume": self.resume,
        }


@dataclass(frozen=True)
class ChangePage:
    """One filtered raw ``ChangeRecord`` page and its own continuation.

    ``after_watermark``/``after_change_id`` always describe the last raw row read
    from this page, before any grouping by Document. Callers must persist these
    values, not a grouped candidate's highest watermark: one Document can have a
    later raw change after an earlier page, and using the grouped maximum would
    skip it.
    """

    records: tuple[ChangeRecord, ...]
    after_watermark: int
    after_change_id: UUID | None
    limit: int
    completed: bool
    source_ids: tuple[str, ...]
    kinds: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "records": [
                {
                    "id": str(record.id),
                    "watermark": record.watermark,
                    "kind": record.kind,
                    "document_id": str(record.document_id),
                    "fact_id": (
                        str(record.fact_id)
                        if record.fact_id is not None
                        else None
                    ),
                    "recorded_at": record.recorded_at.isoformat(),
                }
                for record in self.records
            ],
            "after_watermark": self.after_watermark,
            "after_change_id": (
                str(self.after_change_id)
                if self.after_change_id is not None
                else None
            ),
            "limit": self.limit,
            "completed": self.completed,
            "source_ids": list(self.source_ids),
            "kinds": list(self.kinds),
        }


def initial_scan_progress(
    window: ChangeWindow,
    *,
    query_snapshot: dict,
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """Build the versioned progress object persisted on a research Run."""

    if not isinstance(query_snapshot, dict) or not query_snapshot:
        raise ChangeCursorError(
            "SCAN_SNAPSHOT_INVALID",
            "A change scan requires a non-empty query snapshot",
        )
    if (
        window_start.utcoffset() is None
        or window_end.utcoffset() is None
        or window_start > window_end
    ):
        raise ChangeCursorError(
            "WINDOW_INVALID",
            "Change window display times must be timezone-aware and ordered",
        )
    return {
        "version": CHANGE_SCAN_PROGRESS_VERSION,
        "status": "paging",
        "after_watermark": window.start,
        "after_change_id": None,
        "window": {
            "change_watermark_start": window.start,
            "change_watermark_end": window.end,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
        "query_snapshot": deepcopy(query_snapshot),
        "counts": {
            "raw_changes_scanned": 0,
            "pages_scanned": 0,
            "candidate_documents_scanned": 0,
            "selected_count": 0,
        },
        "scanned_document_ids": [],
        "selection": {
            "status": "pending",
            "evidence_package_id": None,
            "selected_count": 0,
        },
        "updated_at": datetime.now(UTC).isoformat(),
    }


def validate_scan_progress(raw: object, window: ChangeWindow) -> dict:
    """Decode and validate the current Run scan progress.

    SQLAlchemy stores these values as JSONB. Shape checks stay in Python so a
    partially written or externally modified object cannot be mistaken for a
    completed scan and advance the successful cursor.
    """

    if not isinstance(raw, dict):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan progress must be an object",
        )
    if raw.get("version") != CHANGE_SCAN_PROGRESS_VERSION:
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan progress version is unsupported",
        )
    status = raw.get("status")
    if status not in {"paging", "scanned"}:
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan status is invalid",
        )
    after_watermark = raw.get("after_watermark")
    if (
        not isinstance(after_watermark, int)
        or isinstance(after_watermark, bool)
        or not window.start <= after_watermark <= window.end
    ):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan position is outside its fixed window",
        )
    after_change_id = raw.get("after_change_id")
    if after_change_id is not None:
        try:
            UUID(str(after_change_id))
        except (TypeError, ValueError) as exc:
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                "Task Run change scan position id is invalid",
            ) from exc
    snapshot = raw.get("query_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan query snapshot is invalid",
        )
    counts = raw.get("counts")
    if not isinstance(counts, dict):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan counts are invalid",
        )
    for key in (
        "raw_changes_scanned",
        "pages_scanned",
        "candidate_documents_scanned",
        "selected_count",
    ):
        value = counts.get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                f"Task Run change scan count {key} is invalid",
            )
    document_ids = raw.get("scanned_document_ids")
    if not isinstance(document_ids, list) or len(document_ids) != len(
        set(document_ids)
    ):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run scanned Document identities are invalid",
        )
    for value in document_ids:
        try:
            UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                "Task Run scanned Document identity is invalid",
            ) from exc
    previous_window = raw.get("window")
    if not isinstance(previous_window, dict):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change window display snapshot is invalid",
        )
    if (
        previous_window.get("change_watermark_start") != window.start
        or previous_window.get("change_watermark_end") != window.end
    ):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan progress belongs to another window",
        )
    for key in ("window_start", "window_end"):
        value = previous_window.get(key)
        if not isinstance(value, str):
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                "Task Run change window display time is invalid",
            )
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                "Task Run change window display time is invalid",
            ) from exc
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                "Task Run change window display time must include a timezone",
            )
    window_start = datetime.fromisoformat(previous_window["window_start"])
    window_end = datetime.fromisoformat(previous_window["window_end"])
    if window_start > window_end:
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change window display times are out of order",
        )
    snapshot = raw["query_snapshot"]
    snapshot_queries = snapshot.get("queries")
    snapshot_sources = snapshot.get("source_ids")
    if (
        not isinstance(snapshot_queries, list)
        or any(
            not isinstance(item, str) or not item.strip()
            for item in snapshot_queries
        )
        or not isinstance(snapshot_sources, list)
        or len(snapshot_sources) != len(set(snapshot_sources))
        or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 120
            for item in snapshot_sources
        )
    ):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan query snapshot is invalid",
        )
    for key, minimum, maximum in (
        ("limit_per_query", 1, 50),
        ("max_tokens", 1, 64_000),
        ("page_limit", 1, 1_000),
    ):
        value = snapshot.get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                f"Task Run change scan query snapshot {key} is invalid",
            )
    selection = raw.get("selection")
    if (
        not isinstance(selection, dict)
        or selection.get("status") not in {"pending", "succeeded"}
        or not isinstance(selection.get("selected_count"), int)
        or isinstance(selection.get("selected_count"), bool)
        or selection["selected_count"] < 0
    ):
        raise ChangeCursorError(
            "SCAN_PROGRESS_INVALID",
            "Task Run change scan selection state is invalid",
        )
    package_id = selection.get("evidence_package_id")
    if selection["status"] == "pending":
        if package_id is not None:
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                "Pending Task Run scan state cannot reference an Evidence Package",
            )
    else:
        try:
            UUID(str(package_id))
        except (TypeError, ValueError) as exc:
            raise ChangeCursorError(
                "SCAN_PROGRESS_INVALID",
                "Task Run scan state references an invalid Evidence Package",
            ) from exc
    return deepcopy(raw)


def checkpoint_scan(
    session: Session,
    run,
    *,
    page,
    selection: dict,
) -> dict:
    """Persist one filtered raw page checkpoint without committing it.

    The caller must include the corresponding Evidence Package in the same
    transaction. Raw ``after_watermark``/``after_change_id`` are taken from the
    page itself, never from a grouped candidate.
    """

    window = ChangeWindow(
        start=int(run.change_watermark_start),
        end=run.change_watermark_end,
        task_id=run.task_id,
        fingerprint=run.scope_fingerprint,
        resume=True,
    )
    progress = validate_scan_progress(run.change_scan_progress, window)
    counts = progress["counts"]
    scanned_ids = set(progress["scanned_document_ids"])
    scanned_ids.update(page.get("scanned_document_ids", ()))
    counts["raw_changes_scanned"] += int(page["raw_changes_scanned"])
    counts["pages_scanned"] += 1
    counts["candidate_documents_scanned"] = len(scanned_ids)
    counts["selected_count"] = int(counts["selected_count"]) + int(
        selection.get("selected_count", 0)
    )
    progress["after_watermark"] = int(page["after_watermark"])
    progress["after_change_id"] = page.get("after_change_id")
    progress["status"] = "scanned" if page["completed"] else "paging"
    progress["scanned_document_ids"] = sorted(scanned_ids)
    progress["selection"] = {
        "status": "succeeded",
        "evidence_package_id": selection.get("evidence_package_id"),
        "selected_count": int(selection.get("selected_count", 0)),
    }
    progress["updated_at"] = datetime.now(UTC).isoformat()
    run.change_scan_progress = progress
    # Raw exhaustion is a resumable checkpoint, not a completed Run. The final
    # Agent/report transaction sets ``change_scan_completed`` only together with
    # the cursor so a later failure can still resume this fixed window.
    run.change_scan_completed = False
    session.flush()
    return deepcopy(progress)


def open_window(
    session: Session,
    *,
    task_id: str,
    scope: dict,
    initial_lookback: int | None = None,
) -> ChangeWindow:
    """Establish the window for this Run, without committing the cursor.

    ``start`` is the stored cursor for this Task + scope. On a first run it is
    derived from the optional ``initial_lookback``:

    * ``None`` (the default) scans from the very beginning. A new Task therefore
      sees all material that is already stored instead of silently starting at
      "now" and pretending the past never existed.
    * a positive number limits that to the last N changes, for a Task that only
      wants recent material on its first run.
    """

    fingerprint = scope_fingerprint(task_id, scope)
    end = current_watermark(session)
    cursor = session.get(ResearchCursor, (task_id, fingerprint))
    if cursor is not None:
        start = cursor.watermark
        resume = False
    else:
        if initial_lookback is not None and initial_lookback < 0:
            raise ChangeCursorError(
                "WINDOW_INVALID", "initial_lookback must not be negative"
            )
        start = 0 if initial_lookback is None else max(0, end - initial_lookback)
        resume = False
    if start > end:
        # The counter cannot go backwards in a live database; if it did, the safe
        # response is to rescan rather than silently narrow the window.
        start = end
    return ChangeWindow(
        start=start,
        end=end,
        task_id=task_id,
        fingerprint=fingerprint,
        resume=resume,
    )


def changes_in_window(
    session: Session,
    window: ChangeWindow,
    *,
    kinds: tuple[str, ...] | None = None,
    limit: int = 200,
    after_watermark: int | None = None,
) -> list[ChangeRecord]:
    """Read change records inside ``(start, end]``, paginated by watermark.

    ``after_watermark`` resumes a partial scan: the caller passes the last watermark
    it processed, and the same window and filters continue from there. A bounded
    page plus the last watermark is enough to continue, so an interrupted Run never
    has to re-derive its position.
    """

    return list(
        search_changes(
            session,
            window,
            kinds=kinds,
            limit=limit,
            after_watermark=after_watermark,
        ).records
    )


def search_changes(
    session: Session,
    window: ChangeWindow,
    *,
    kinds: tuple[str, ...] | None = None,
    source_ids: tuple[str, ...] = (),
    limit: int = 200,
    after_watermark: int | None = None,
    after_change_id: UUID | str | None = None,
) -> ChangePage:
    """Read one raw page, applying all filters before ``LIMIT``.

    Filtering and limiting in one SQL statement is essential for resumable scans.
    If a page is read from all sources first and filtered afterwards, an empty
    filtered page can still hide later matching records. The extra row lets the
    caller distinguish a full final page from one that has more filtered rows.
    """

    if not 1 <= limit <= 1000:
        raise ChangeCursorError(
            "PAGE_INVALID", "limit must be between 1 and 1000"
        )
    normalized_kinds = tuple(kinds or ())
    if normalized_kinds:
        unknown = [
            kind for kind in normalized_kinds if kind not in CHANGE_KINDS
        ]
        if unknown:
            raise ChangeCursorError(
                "KIND_INVALID", f"unknown change kind: {unknown[0]}"
            )
    normalized_sources = tuple(source_ids)
    if (
        len(set(normalized_sources)) != len(normalized_sources)
        or any(
            not isinstance(source_id, str)
            or not source_id.strip()
            or len(source_id) > 120
            for source_id in normalized_sources
        )
    ):
        raise ChangeCursorError(
            "SOURCE_INVALID",
            "source_ids must be unique non-empty values of at most 120 chars",
        )
    if after_watermark is not None:
        if (
            not isinstance(after_watermark, int)
            or isinstance(after_watermark, bool)
            or after_watermark < window.start
            or after_watermark > window.end
        ):
            raise ChangeCursorError(
                "PAGE_INVALID",
                "after_watermark must be inside the fixed change window",
            )
    floor = window.start if after_watermark is None else after_watermark
    if after_change_id is None:
        position_filter = ChangeRecord.watermark > floor
    else:
        try:
            change_id = (
                after_change_id
                if isinstance(after_change_id, UUID)
                else UUID(str(after_change_id))
            )
        except (TypeError, ValueError) as exc:
            raise ChangeCursorError(
                "PAGE_INVALID", "after_change_id is invalid"
            ) from exc
        # A watermark is unique in the current schema, but the complete ordered
        # key remains the continuation contract. This avoids depending on that
        # uniqueness if a future current storage design changes.
        position_filter = or_(
            ChangeRecord.watermark > floor,
            and_(
                ChangeRecord.watermark == floor,
                ChangeRecord.id > change_id,
            ),
        )
    statement = (
        select(ChangeRecord)
        .join(Document, Document.id == ChangeRecord.document_id)
        .where(
            position_filter,
            ChangeRecord.watermark <= window.end,
        )
        .order_by(ChangeRecord.watermark, ChangeRecord.id)
        .limit(limit + 1)
    )
    if normalized_kinds:
        statement = statement.where(ChangeRecord.kind.in_(normalized_kinds))
    if normalized_sources:
        statement = statement.where(Document.source_id.in_(normalized_sources))
    rows = list(session.scalars(statement).all())
    completed = len(rows) <= limit
    records = rows[:limit]
    if records:
        next_watermark = records[-1].watermark
        next_change_id = records[-1].id
    else:
        next_watermark = floor
        next_change_id = (
            UUID(str(after_change_id))
            if after_change_id is not None
            else None
        )
    return ChangePage(
        records=tuple(records),
        after_watermark=next_watermark,
        after_change_id=next_change_id,
        limit=limit,
        completed=completed,
        source_ids=normalized_sources,
        kinds=normalized_kinds,
    )


def scan_complete(window: ChangeWindow, records: list[ChangeRecord], limit: int) -> bool:
    """Whether a page ended the scan for this window.

    A short page means nothing is left. A full page means more may remain, so the
    caller must continue from the last watermark instead of advancing the cursor.
    """

    if not records:
        return True
    return len(records) < limit


def advance_cursor(
    session: Session,
    window: ChangeWindow,
    *,
    updated_at: datetime,
) -> ResearchCursor:
    """Move this Task + scope past ``window.end``, in the caller's transaction.

    Called only when the window scan completed *and* the Run's analysis and report
    were persisted, so the cursor can never move past material that was not
    actually scanned or never produced a result.
    """

    if updated_at.utcoffset() is None:
        raise ValueError("updated_at must include a timezone")
    cursor = session.scalar(
        select(ResearchCursor)
        .where(
            ResearchCursor.task_id == window.task_id,
            ResearchCursor.scope_fingerprint == window.fingerprint,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if cursor is None:
        cursor = ResearchCursor(
            task_id=window.task_id,
            scope_fingerprint=window.fingerprint,
            watermark=window.end,
            updated_at=updated_at,
        )
        session.add(cursor)
    else:
        # Never move backwards, even if two Runs somehow interleave.
        cursor.watermark = max(cursor.watermark, window.end)
        cursor.updated_at = updated_at
    session.flush()
    return cursor


def get_cursor(session: Session, task_id: str, scope: dict) -> ResearchCursor | None:
    return session.get(ResearchCursor, (task_id, scope_fingerprint(task_id, scope)))


@dataclass(frozen=True)
class ChangedCandidate:
    """One researchable item discovered inside a change window."""

    document_id: UUID
    watermark: int
    kinds: tuple[str, ...]
    fact_ids: tuple[UUID, ...]
    published_at: datetime | None
    collected_at: datetime

    def to_dict(self) -> dict:
        return {
            "document_id": str(self.document_id),
            "watermark": self.watermark,
            "kinds": list(self.kinds),
            "fact_ids": [str(value) for value in self.fact_ids],
            "published_at": (
                self.published_at.isoformat()
                if self.published_at is not None
                else None
            ),
            "collected_at": self.collected_at.isoformat(),
        }


def changed_candidates(
    session: Session,
    window: ChangeWindow,
    *,
    source_ids: tuple[str, ...] = (),
    kinds: tuple[str, ...] | None = None,
    limit: int = 200,
    after_watermark: int | None = None,
    after_change_id: UUID | str | None = None,
) -> list[ChangedCandidate]:
    """Group this window's changes into one candidate per Document version.

    Grouping matters because one Document commonly produces several change records
    in the same window (ingested, then parsed, then classified). Reporting them as
    separate candidates would make a single source look like several independent
    pieces of evidence. Candidate grouping is intentionally page-local. Its
    watermark is useful for ordering the page, never for scan continuation.
    """

    page = search_changes(
        session,
        window,
        kinds=kinds,
        source_ids=source_ids,
        limit=limit,
        after_watermark=after_watermark,
        after_change_id=after_change_id,
    )
    records = list(page.records)
    if not records:
        return []
    document_ids = list(dict.fromkeys(record.document_id for record in records))
    documents = {
        document.id: document
        for document in session.scalars(
            select(Document).where(Document.id.in_(document_ids))
        ).all()
    }
    grouped: dict[UUID, dict] = {}
    for record in records:
        entry = grouped.setdefault(
            record.document_id,
            {"watermark": 0, "kinds": [], "fact_ids": [], "first": record.watermark},
        )
        entry["watermark"] = max(entry["watermark"], record.watermark)
        if record.kind not in entry["kinds"]:
            entry["kinds"].append(record.kind)
        if record.fact_id is not None and record.fact_id not in entry["fact_ids"]:
            entry["fact_ids"].append(record.fact_id)
    candidates: list[ChangedCandidate] = []
    for document_id, entry in grouped.items():
        document = documents.get(document_id)
        if document is None:
            continue
        candidates.append(
            ChangedCandidate(
                document_id=document_id,
                watermark=entry["watermark"],
                kinds=tuple(entry["kinds"]),
                fact_ids=tuple(entry["fact_ids"]),
                published_at=document.published_at,
                collected_at=document.collected_at,
            )
        )
    candidates.sort(key=lambda item: (item.watermark, str(item.document_id)))
    return candidates


def change_count(session: Session) -> int:
    """Total recorded changes; used by tests and health output."""

    return int(session.scalar(select(func.count(ChangeRecord.id))) or 0)


def fact_change_ids(session: Session, document_id: UUID) -> list[UUID]:
    """Fact ids recorded as changed for one Document version."""

    return list(
        session.scalars(
            select(ChangeRecord.fact_id)
            .where(
                ChangeRecord.document_id == document_id,
                ChangeRecord.fact_id.is_not(None),
            )
            .order_by(ChangeRecord.watermark)
        ).all()
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CHANGE_KINDS",
    "CHANGE_SCAN_PROGRESS_VERSION",
    "COUNTER_NAME",
    "ChangeCursorError",
    "ChangePage",
    "ChangeRecord",
    "ChangeWindow",
    "ChangedCandidate",
    "advance_cursor",
    "change_count",
    "changed_candidates",
    "changes_in_window",
    "checkpoint_scan",
    "current_watermark",
    "ensure_counter",
    "fact_change_ids",
    "get_cursor",
    "initial_scan_progress",
    "open_window",
    "record_change",
    "scan_complete",
    "search_changes",
    "scope_fingerprint",
    "validate_scan_progress",
]
