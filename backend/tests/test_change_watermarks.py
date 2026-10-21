"""Committed-visible change watermarks and resumable incremental scans.

The critical cases need a real PostgreSQL instance (``TEST_DATABASE_URL``) because
the whole point is transaction visibility: a watermark is only trustworthy if it
proves that no uncommitted writer sits behind it. Stubbing that would test nothing.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.changes import (
    CHANGE_KINDS,
    ChangeCursorError,
    ChangeWindow,
    advance_cursor,
    change_count,
    changed_candidates,
    changes_in_window,
    checkpoint_scan,
    current_watermark,
    ensure_counter,
    fact_change_ids,
    get_cursor,
    initial_scan_progress,
    open_window,
    record_change,
    scan_complete,
    scope_fingerprint,
    search_changes,
)
from stocks.models import (
    Base,
    ChangeCounter,
    ChangeRecord,
    Document,
    DocumentProcessingState,
    Fact,
    Source,
    TaskDefinition,
    TaskRun,
    TaskStepResult,
)
from stocks.repository import DocumentInput, ingest_document
from stocks.settings import Settings


def make_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
        "parse_chunk_size": 400,
        "parse_chunk_overlap": 40,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for watermark tests")
    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def _store(config, source_id, payload, content_type):
    return (
        f"sources/{source_id}/{hashlib.sha256(payload).hexdigest()}",
        hashlib.sha256(payload).hexdigest(),
    )


def seed_source(engine, source_id: str = "sec") -> None:
    with Session(engine) as session:
        if session.get(Source, source_id) is None:
            session.add(
                Source(id=source_id, name=source_id, connector="rss", trust_tier="T1")
            )
            session.commit()


def seed_task(engine, task_id: str = "daily") -> None:
    now = datetime(2026, 10, 21, tzinfo=UTC)
    with Session(engine) as session:
        if session.get(TaskDefinition, task_id) is None:
            session.add(
                TaskDefinition(
                    id=task_id,
                    name=task_id,
                    type="research",
                    trigger="manual",
                    enabled=True,
                    strategy=None,
                    scope={"regions": ["US"]},
                    config_snapshot={},
                    config_hash="h" * 64,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()


def ingest(
    engine,
    config: Settings,
    *,
    dedupe_key: str = "release-1",
    collected_at: datetime | None = None,
    payload: bytes = b"<html><body><article><p>Inflation was 3.1%.</p></article></body></html>",
    source_id: str = "sec",
) -> Document:
    seed_source(engine, source_id)
    with Session(engine) as session:
        item = DocumentInput(
            source_id=source_id,
            dedupe_key=dedupe_key,
            title="Release",
            payload=payload,
            content_type="text/html; charset=utf-8",
            collected_at=collected_at or datetime(2026, 10, 21, 1, tzinfo=UTC),
            published_at=datetime(2026, 10, 20, tzinfo=UTC),
            completeness="complete",
        )
        return ingest_document(session, config, item, store=_store).document


# --------------------------------------------------------------------------- #
# Watermark ordering
# --------------------------------------------------------------------------- #


def test_ingest_records_one_committed_change(engine):
    config = make_config()

    document = ingest(engine, config)

    with Session(engine) as session:
        assert current_watermark(session) == 1
        assert change_count(session) == 1
        records = session.scalars(select(ChangeRecord)).all()
    assert records[0].kind == "document_ingested"
    assert records[0].document_id == document.id
    assert records[0].watermark == 1


def test_watermark_is_not_the_collection_timestamp(engine):
    """A Document that arrives late still gets the next watermark, not an old one."""

    config = make_config()
    early = ingest(
        engine,
        config,
        dedupe_key="early",
        collected_at=datetime(2026, 10, 21, 1, tzinfo=UTC),
    )
    late = ingest(
        engine,
        config,
        dedupe_key="late",
        collected_at=datetime(2026, 10, 19, 1, tzinfo=UTC),  # older content time
    )

    with Session(engine) as session:
        rows = {
            record.document_id: record.watermark
            for record in session.scalars(select(ChangeRecord)).all()
        }
    # The late-arriving, older-dated Document still has the higher watermark: it
    # became researchable later, which is what a window tracks.
    assert rows[late.id] > rows[early.id]


def test_a_transaction_that_commits_later_gets_a_higher_watermark(engine):
    """AC-1: 07:59 start / 08:01 commit must land in the next window.

    A watcher commits a change first; a slower transaction that started earlier but
    commits afterwards must receive the higher watermark, so a window closed before
    it cannot already contain it.
    """

    config = make_config()
    seed_source(engine)

    slow = Session(engine)
    fast_done = threading.Event()
    slow_created = threading.Event()
    watcher_watermark: list[int] = []

    def fast_writer() -> None:
        # Phase 1: the fast writer commits and reads the watermark, which must not
        # include the still-open slow transaction.
        with Session(engine) as session:
            first = ingest_document(
                session,
                config,
                DocumentInput(
                    source_id="sec",
                    dedupe_key="fast",
                    title="Fast",
                    payload=b"<html><body><article><p>Fast.</p></article></body></html>",
                    content_type="text/html; charset=utf-8",
                    collected_at=datetime(2026, 10, 21, 7, 59, tzinfo=UTC),
                    completeness="complete",
                ),
                store=_store,
            ).document
            assert first is not None
        with Session(engine) as session:
            watcher_watermark.append(current_watermark(session))
        fast_done.set()

    # The slow writer starts its change first and stays open.
    seed_source(engine)
    slow.begin()
    slow_document = Document(
        source_id="sec",
        dedupe_key="slow",
        version=1,
        title="Slow",
        collected_at=datetime(2026, 10, 21, 7, 59, tzinfo=UTC),
        completeness="complete",
        content_type="text/html; charset=utf-8",
        content_size=10,
        content_hash="a" * 64,
        snapshot_key="sources/sec/slow",
        trust_tier_snapshot="T1",
    )
    slow.add(slow_document)
    slow.flush()
    slow_watermark = record_change(
        slow,
        kind="document_ingested",
        document_id=slow_document.id,
        recorded_at=datetime(2026, 10, 21, 7, 59, tzinfo=UTC),
    )
    slow_created.set()

    thread = threading.Thread(target=fast_writer)
    thread.start()
    # The fast writer blocks on the counter row while the slow transaction holds it,
    # which is exactly the serialization the design relies on.
    deadline = time.monotonic() + 10
    while not fast_done.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
    fast_done_before_slow_commit = fast_done.is_set()
    slow.commit()
    thread.join(timeout=20)
    slow.close()

    assert slow_created.is_set()
    assert not fast_done_before_slow_commit, (
        "the fast writer must wait for the open change transaction"
    )
    assert watcher_watermark and watcher_watermark[0] >= slow_watermark

    with Session(engine) as session:
        # The slow change is at a lower watermark than the fast one because it
        # committed first, even though it started first too.
        rows = {
            record.watermark
            for record in session.scalars(select(ChangeRecord)).all()
        }
    assert len(rows) == 2
    assert min(rows) < max(rows)


def test_window_end_contains_every_committed_change_behind_it(engine):
    """A reader's ``end`` never hides an uncommitted hole behind it."""

    seed_task(engine, "daily")

    open_session = Session(engine)
    open_session.begin()
    seed_source(engine)
    blocked = Document(
        source_id="sec",
        dedupe_key="blocked",
        version=1,
        title="Blocked",
        collected_at=datetime(2026, 10, 21, 7, 59, tzinfo=UTC),
        completeness="complete",
        content_type="text/html; charset=utf-8",
        content_size=1,
        content_hash="b" * 64,
        snapshot_key="sources/sec/blocked",
        trust_tier_snapshot="T1",
    )
    open_session.add(blocked)
    open_session.flush()
    record_change(
        open_session,
        kind="document_ingested",
        document_id=blocked.id,
        recorded_at=datetime(2026, 10, 21, 7, 59, tzinfo=UTC),
    )

    # A separate reader at 08:00 sees a watermark that excludes the open change.
    with Session(engine) as session:
        window_0800 = open_window(session, task_id="daily", scope={"regions": ["US"]})
        # This read must not block forever on the open row lock: it is a plain
        # SELECT of the counter, so it sees only committed state.
        assert window_0800.end == 0

    blocked_id = blocked.id
    open_session.commit()
    open_session.close()

    with Session(engine) as session:
        window_later = open_window(session, task_id="daily", scope={"regions": ["US"]})
        assert window_later.end == 1
        assert window_later.start == window_0800.end
        # The change that committed late falls inside the later window.
        records = changes_in_window(session, window_later)
        assert [record.document_id for record in records] == [blocked_id]


def test_failed_transaction_leaves_no_change_and_no_gap(engine):
    config = make_config()
    seed_source(engine)

    session = Session(engine)
    session.begin()
    document = Document(
        source_id="sec",
        dedupe_key="rolled-back",
        version=1,
        title="Rolled back",
        collected_at=datetime(2026, 10, 21, tzinfo=UTC),
        completeness="complete",
        content_type="text/html; charset=utf-8",
        content_size=1,
        content_hash="c" * 64,
        snapshot_key="sources/sec/rb",
        trust_tier_snapshot="T1",
    )
    session.add(document)
    session.flush()
    record_change(
        session,
        kind="document_ingested",
        document_id=document.id,
        recorded_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    session.rollback()
    session.close()

    with Session(engine) as session:
        # A rolled-back change consumes no watermark, so the next committed change
        # still gets 1 and no window can contain an empty slot.
        assert current_watermark(session) == 0
        assert change_count(session) == 0
    ingest(engine, config, dedupe_key="after-rollback")
    with Session(engine) as session:
        rows = session.scalars(select(ChangeRecord)).all()
    assert [row.watermark for row in rows] == [1]


# --------------------------------------------------------------------------- #
# Windows, pagination and cursors
# --------------------------------------------------------------------------- #


def test_cursor_is_per_task_and_per_scope(engine):
    """AC-4: two Tasks researching the same material do not consume each other."""

    config = make_config()
    seed_task(engine, "daily")
    seed_task(engine, "weekly")
    document = ingest(engine, config)

    with Session(engine) as session:
        daily = open_window(session, task_id="daily", scope={"regions": ["US"]})
        weekly = open_window(
            session, task_id="weekly", scope={"regions": ["US"]}
        )
        narrowed = open_window(session, task_id="daily", scope={"symbols": ["NVDA"]})
        advance_cursor(session, daily, updated_at=datetime(2026, 10, 21, tzinfo=UTC))
        session.commit()

    with Session(engine) as session:
        # Advancing the daily cursor did not move the weekly one.
        assert get_cursor(session, "daily", {"regions": ["US"]}).watermark == daily.end
        assert get_cursor(session, "weekly", {"regions": ["US"]}) is None
        assert get_cursor(session, "daily", {"symbols": ["NVDA"]}) is None
        weekly_records = changes_in_window(session, weekly)
    assert [record.document_id for record in weekly_records] == [document.id]
    assert narrowed.end == daily.end


def test_changed_scope_reinitializes_instead_of_reusing_the_cursor(engine):
    config = make_config()
    seed_task(engine, "daily")
    ingest(engine, config, dedupe_key="first")

    with Session(engine) as session:
        original = open_window(session, task_id="daily", scope={"regions": ["US"]})
        advance_cursor(session, original, updated_at=datetime(2026, 10, 21, tzinfo=UTC))
        session.commit()

    ingest(engine, config, dedupe_key="second")
    with Session(engine) as session:
        changed = open_window(
            session, task_id="daily", scope={"regions": ["US", "EU"]}
        )
        assert changed.fingerprint != original.fingerprint
        # The new scope starts from its own initial point rather than inheriting the
        # old range, so nothing is silently skipped for the wider scope.
        assert changed.start == 0
        assert len(changes_in_window(session, changed)) == 2


def test_first_run_scans_from_the_beginning(engine):
    """A new Task must see already-stored material, not start at "now"."""

    config = make_config()
    seed_task(engine, "daily")
    for index in range(3):
        ingest(engine, config, dedupe_key=f"doc-{index}")

    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        assert window.start == 0
        assert window.end == 3
        records = changes_in_window(session, window)
    assert [record.watermark for record in records] == [1, 2, 3]


def test_initial_lookback_can_limit_a_first_run(engine):
    config = make_config()
    seed_task(engine, "daily")
    for index in range(3):
        ingest(engine, config, dedupe_key=f"doc-{index}")

    with Session(engine) as session:
        window = open_window(
            session, task_id="daily", scope={"regions": ["US"]}, initial_lookback=2
        )
        assert window.start == 1
        assert window.end == 3
        records = changes_in_window(session, window)
    assert [record.watermark for record in records] == [2, 3]


def test_pagination_resumes_the_same_window(engine):
    """AC-3: a partial scan continues from its last watermark in the same window."""

    config = make_config()
    seed_task(engine, "daily")
    for index in range(5):
        ingest(engine, config, dedupe_key=f"doc-{index}")

    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        first_page = changes_in_window(session, window, limit=2)
        assert [record.watermark for record in first_page] == [1, 2]
        assert scan_complete(window, first_page, 2) is False
        second_page = changes_in_window(
            session, window, limit=2, after_watermark=first_page[-1].watermark
        )
        assert [record.watermark for record in second_page] == [3, 4]
        last_page = changes_in_window(
            session, window, limit=2, after_watermark=second_page[-1].watermark
        )
        assert [record.watermark for record in last_page] == [5]
        assert scan_complete(window, last_page, 2) is True


def test_filtered_pages_limit_in_sql_and_preserve_raw_continuation(engine):
    """A page of unmatching rows must not hide later matching rows."""

    config = make_config()
    seed_task(engine, "daily")
    seed_source(engine, "media")
    for index in range(3):
        ingest(
            engine,
            config,
            dedupe_key=f"media-{index}",
            source_id="media",
        )
    target = ingest(engine, config, dedupe_key="sec-target")

    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        page = search_changes(
            session,
            window,
            source_ids=("sec",),
            limit=1,
        )
    assert [record.document_id for record in page.records] == [target.id]
    assert page.completed is True
    assert page.after_watermark == 4


def test_candidate_grouping_cannot_move_raw_pagination(engine):
    """Grouped candidate watermarks are display data, never continuation."""

    config = make_config()
    seed_task(engine, "daily")
    document = ingest(engine, config, dedupe_key="shared")
    with Session(engine) as session:
        for _ in range(2):
            record_change(
                session,
                kind="document_parsed",
                document_id=document.id,
                recorded_at=datetime(2026, 10, 21, 2, tzinfo=UTC),
            )
        session.commit()

    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        first = search_changes(session, window, limit=2)
        candidates = changed_candidates(
            session,
            window,
            limit=2,
        )
        second = search_changes(
            session,
            window,
            limit=2,
            after_watermark=first.after_watermark,
            after_change_id=first.after_change_id,
        )
    assert [record.watermark for record in first.records] == [1, 2]
    assert candidates[0].watermark == 2
    assert [record.watermark for record in second.records] == [3]


def test_cursor_only_advances_after_a_completed_scan(engine):
    """AC-3: an unfinished scan must not move the cursor past what it examined."""

    config = make_config()
    seed_task(engine, "daily")
    for index in range(4):
        ingest(engine, config, dedupe_key=f"doc-{index}")

    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        page = changes_in_window(session, window, limit=2)
    assert scan_complete(window, page, 2) is False
    # The caller does not advance; the next Run reopens the same start.
    with Session(engine) as session:
        still_open = open_window(session, task_id="daily", scope={"regions": ["US"]})
    assert still_open.start == 0
    assert still_open.end == window.end

    with Session(engine) as session:
        advance_cursor(session, window, updated_at=datetime(2026, 10, 21, tzinfo=UTC))
        session.commit()
    with Session(engine) as session:
        after = open_window(session, task_id="daily", scope={"regions": ["US"]})
    assert after.start == window.end


def test_cursor_never_moves_backwards(engine):
    config = make_config()
    seed_task(engine, "daily")
    ingest(engine, config)
    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        advance_cursor(session, window, updated_at=datetime(2026, 10, 21, tzinfo=UTC))
        session.commit()
    older = window.__class__(
        start=0, end=0, task_id=window.task_id, fingerprint=window.fingerprint,
        resume=False,
    )
    with Session(engine) as session:
        advance_cursor(session, older, updated_at=datetime(2026, 10, 21, tzinfo=UTC))
        session.commit()
        assert get_cursor(session, "daily", {"regions": ["US"]}).watermark == 1


def test_scope_fingerprint_is_order_independent_and_task_scoped():
    assert scope_fingerprint("a", {"x": 1, "y": 2}) == scope_fingerprint(
        "a", {"y": 2, "x": 1}
    )
    assert scope_fingerprint("a", {"x": 1}) != scope_fingerprint("b", {"x": 1})
    assert scope_fingerprint("a", {"x": 1}) != scope_fingerprint("a", {"x": 2})
    assert len(scope_fingerprint("a", {"x": 1})) == 64
    with pytest.raises(ChangeCursorError):
        scope_fingerprint("", {"x": 1})
    with pytest.raises(ChangeCursorError):
        scope_fingerprint("a", ["not", "a", "mapping"])


def test_change_record_validation_is_deterministic(engine):
    config = make_config()
    document = ingest(engine, config)

    with Session(engine) as session:
        with pytest.raises(ValueError, match="unknown change kind"):
            record_change(
                session,
                kind="something_else",
                document_id=document.id,
                recorded_at=datetime(2026, 10, 21, tzinfo=UTC),
            )
        with pytest.raises(ValueError, match="fact id"):
            record_change(
                session,
                kind="fact_extracted",
                document_id=document.id,
                recorded_at=datetime(2026, 10, 21, tzinfo=UTC),
            )
        with pytest.raises(ValueError, match="timezone"):
            record_change(
                session,
                kind="document_ingested",
                document_id=document.id,
                recorded_at=datetime(2026, 10, 21),
            )
    assert set(CHANGE_KINDS) >= {"document_ingested", "fact_extracted"}


def test_changed_candidates_group_one_document_into_one_candidate(engine):
    """AC-2: several changes to one version are one piece of fresh material."""

    config = make_config()
    seed_task(engine, "daily")
    document = ingest(engine, config)
    with Session(engine) as session:
        now = datetime(2026, 10, 21, 2, tzinfo=UTC)
        record_change(
            session,
            kind="document_parsed",
            document_id=document.id,
            recorded_at=now,
        )
        record_change(
            session,
            kind="document_classified",
            document_id=document.id,
            recorded_at=now,
        )
        session.commit()

    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        candidates = changed_candidates(session, window)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.document_id == document.id
    assert set(candidate.kinds) == {
        "document_ingested",
        "document_parsed",
        "document_classified",
    }
    # The candidate watermark is the highest change so resuming skips what was
    # already examined without re-reporting the same Document.
    assert candidate.watermark == 3
    # The real source time is preserved separately from the watermark.
    assert candidate.published_at == datetime(2026, 10, 20, tzinfo=UTC)


def test_fact_changes_are_traceable_to_their_fact(engine):
    config = make_config()
    seed_task(engine, "daily")
    document = ingest(engine, config)
    with Session(engine) as session:
        fact = Fact(
            document_id=document.id,
            fact_key="k" * 64,
            fact_type="macro_indicator",
            origin_method="source_mapping",
            evidence_ref={"kind": "json_pointer", "pointer": "/v"},
            value=3.1,
            unit="percent",
            attributes={},
            created_at=datetime(2026, 10, 21, 3, tzinfo=UTC),
        )
        session.add(fact)
        session.flush()
        record_change(
            session,
            kind="fact_mapped",
            document_id=document.id,
            fact_id=fact.id,
            recorded_at=datetime(2026, 10, 21, 3, tzinfo=UTC),
        )
        session.commit()
        fact_id = fact.id

    with Session(engine) as session:
        assert fact_change_ids(session, document.id) == [fact_id]
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        candidates = changed_candidates(session, window)
    assert candidates[0].fact_ids == (fact_id,)


def test_candidates_can_be_filtered_by_source_and_kind(engine):
    config = make_config()
    seed_task(engine, "daily")
    first = ingest(engine, config, dedupe_key="a", source_id="sec")
    seed_source(engine, "media")
    second = ingest(engine, config, dedupe_key="b", source_id="media")

    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        only_media = changed_candidates(session, window, source_ids=("media",))
        only_facts = changed_candidates(
            session, window, kinds=("fact_extracted",)
        )
    assert [item.document_id for item in only_media] == [second.id]
    assert only_facts == []
    assert first.id != second.id


def test_window_scan_does_not_depend_on_collection_time(engine):
    """Content dated before the window can still be a new change in it."""

    config = make_config()
    seed_task(engine, "daily")
    ingest(engine, config, dedupe_key="old-1")
    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        advance_cursor(session, window, updated_at=datetime(2026, 10, 21, tzinfo=UTC))
        session.commit()

    # A late-arriving announcement with an older collection time but a new commit.
    late = ingest(
        engine,
        config,
        dedupe_key="old-2",
        collected_at=datetime(2026, 10, 15, tzinfo=UTC),
    )
    with Session(engine) as session:
        later = open_window(session, task_id="daily", scope={"regions": ["US"]})
        candidates = changed_candidates(session, later)
    assert [item.document_id for item in candidates] == [late.id]
    assert candidates[0].published_at == datetime(2026, 10, 20, tzinfo=UTC)


def test_open_window_clamps_a_cursor_ahead_of_the_counter(engine):
    config = make_config()
    seed_task(engine, "daily")
    ingest(engine, config)
    with Session(engine) as session:
        window = open_window(session, task_id="daily", scope={"regions": ["US"]})
        advance_cursor(session, window, updated_at=datetime(2026, 10, 21, tzinfo=UTC))
        # Pretend the stored cursor is somehow ahead of the counter.
        cursor = get_cursor(session, "daily", {"regions": ["US"]})
        cursor.watermark = 99
        session.commit()
        clamped = open_window(session, task_id="daily", scope={"regions": ["US"]})
    assert clamped.start == clamped.end


def test_ensure_counter_is_idempotent(engine):
    with Session(engine) as session:
        ensure_counter(session)
        ensure_counter(session)
        session.commit()
    with Session(engine) as session:
        rows = session.scalars(select(ChangeCounter)).all()
    assert len(rows) == 1
    assert rows[0].name == "research_change"
    assert rows[0].watermark == 0


# --------------------------------------------------------------------------- #
# Run integration
# --------------------------------------------------------------------------- #


def test_research_run_records_a_resumable_window(engine):
    """AC-3: binding persists the fixed window and its initial raw position."""

    from stocks.execution.config import TaskConfig

    config = make_config()
    seed_task(engine, "daily")
    document = ingest(engine, config)

    task = TaskConfig.model_validate(
        {
            "id": "daily",
            "name": "Daily",
            "type": "research",
            "trigger": "manual",
            "objective": "Summarize saved evidence.",
            "scope": {"regions": ["US"]},
            "steps": [
                {
                    "id": "evidence",
                    "select_evidence": {
                        "kind": "select_evidence",
                        "queries": ["inflation"],
                        "limit_per_query": 1,
                    },
                },
                {
                    "id": "research",
                    "research_agent": {
                        "kind": "research_agent",
                        "agent": "writer",
                        "input": "evidence",
                    },
                },
            ],
        }
    )
    now = datetime(2026, 10, 21, 5, tzinfo=UTC)
    with Session(engine) as session:
        run = TaskRun(
            task_id="daily",
            status="running",
            triggered_by="manual",
            process_token="token",
            objective="Summarize saved evidence.",
            strategy=None,
            scope={"regions": ["US"]},
            config_snapshot={
                "task": task.model_dump(mode="json"),
                "agents": [
                    {
                        "id": "writer",
                        "role": "Writer",
                        "prompt": "Write.",
                        "model_ref": "research",
                        "tools": [],
                        "input_schema": {"type": "object"},
                        "output_schema": {
                            "title": "out",
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["summary", "findings", "open_questions", "evidence_refs"],
                            "properties": {
                                "summary": {"type": "string"},
                                "findings": {"type": "array"},
                                "open_questions": {"type": "array"},
                                "evidence_refs": {"type": "array"},
                            },
                        },
                    }
                ],
            },
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            created_at=now,
            started_at=now,
        )
        session.add(run)
        session.commit()
        run_id = run.id

        # Bind the window directly, which is what execution does on entry.
        from stocks.execution.service import _bind_change_window

        window = _bind_change_window(session, session.get(TaskRun, run_id), task)
        assert window is not None
        session.refresh(session.get(TaskRun, run_id))
        stored = session.get(TaskRun, run_id)
        assert stored.change_watermark_start == 0
        assert stored.change_watermark_end == 1
        assert stored.change_scan_completed is False
        assert stored.scope_fingerprint == window.fingerprint

        assert stored.change_scan_progress["after_watermark"] == 0
        assert stored.window_start is not None
        assert stored.window_end is not None
        assert document.id is not None


def test_collection_run_has_no_change_window(engine):
    from stocks.execution.config import TaskConfig
    from stocks.execution.service import _bind_change_window

    seed_task(engine, "collect")
    task = TaskConfig.model_validate(
        {
            "id": "collect",
            "name": "Collect",
            "type": "collection",
            "trigger": "manual",
            "sources": ["sec"],
            "steps": [{"id": "collect", "collect_sources": {"kind": "collect_sources"}}],
        }
    )
    now = datetime(2026, 10, 21, 6, tzinfo=UTC)
    with Session(engine) as session:
        run = TaskRun(
            task_id="collect",
            status="running",
            triggered_by="manual",
            process_token="token",
            objective="Collect",
            strategy=None,
            scope={"source_ids": ["sec"]},
            config_snapshot={"task": task.model_dump(mode="json"), "agents": []},
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            created_at=now,
        )
        session.add(run)
        session.commit()
        window = _bind_change_window(session, run, task)
        assert window is None
        session.refresh(run)
        assert run.change_watermark_end is None


def test_normal_run_resumes_the_latest_unfinished_checkpoint_for_its_scope(engine):
    from stocks.execution.config import TaskConfig
    from stocks.execution.service import (
        ExecutionError,
        _bind_change_window,
    )

    seed_task(engine, "daily")
    seed_task(engine, "other")
    now = datetime(2026, 10, 21, 6, tzinfo=UTC)
    daily_scope = {"regions": ["US"]}
    daily_fingerprint = scope_fingerprint("daily", daily_scope)

    def research_task(task_id: str, scope: dict) -> TaskConfig:
        return TaskConfig.model_validate(
            {
                "id": task_id,
                "name": task_id,
                "type": "research",
                "trigger": "manual",
                "objective": "Resume the fixed window.",
                "scope": scope,
                "steps": [
                    {
                        "id": "evidence",
                        "select_evidence": {
                            "kind": "select_evidence",
                            "queries": ["inflation"],
                        },
                    },
                    {
                        "id": "research",
                        "research_agent": {
                            "kind": "research_agent",
                            "agent": "writer",
                            "input": "evidence",
                        },
                    },
                ],
            }
        )

    task = research_task("daily", daily_scope)
    other_task = research_task("other", daily_scope)
    snapshot = {
        "task": task.model_dump(mode="json"),
        "agents": [],
    }
    progress = initial_scan_progress(
        ChangeWindow(
            start=2,
            end=7,
            task_id="daily",
            fingerprint=daily_fingerprint,
            resume=False,
        ),
        query_snapshot={
            "queries": ["inflation"],
            "source_ids": [],
            "limit_per_query": 8,
            "max_tokens": 30000,
            "page_limit": 100,
        },
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(hours=1),
    )
    progress["after_watermark"] = 4

    with Session(engine) as session:
        prior = TaskRun(
            task_id="daily",
            status="failed",
            triggered_by="manual",
            objective="Prior",
            scope=daily_scope,
            config_snapshot=snapshot,
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            change_watermark_start=2,
            change_watermark_end=7,
            window_start=now - timedelta(hours=2),
            window_end=now - timedelta(hours=1),
            scope_fingerprint=daily_fingerprint,
            change_scan_progress=progress,
            change_scan_completed=False,
            created_at=now - timedelta(minutes=30),
            finished_at=now - timedelta(minutes=20),
        )
        other_scope = TaskRun(
            task_id="other",
            status="failed",
            triggered_by="manual",
            objective="Other",
            scope=daily_scope,
            config_snapshot={
                "task": other_task.model_dump(mode="json"),
                "agents": [],
            },
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            change_watermark_start=0,
            change_watermark_end=3,
            window_start=now - timedelta(hours=2),
            window_end=now - timedelta(hours=1),
            scope_fingerprint=scope_fingerprint("other", daily_scope),
            change_scan_progress=None,
            change_scan_completed=False,
            created_at=now - timedelta(minutes=40),
            finished_at=now - timedelta(minutes=30),
        )
        latest = TaskRun(
            task_id="daily",
            status="failed",
            triggered_by="manual",
            objective="Latest",
            scope=daily_scope,
            config_snapshot=snapshot,
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            change_watermark_start=2,
            change_watermark_end=7,
            window_start=now - timedelta(hours=2),
            window_end=now - timedelta(hours=1),
            scope_fingerprint=daily_fingerprint,
            change_scan_progress={
                **progress,
                "after_watermark": 6,
                "counts": {
                    **progress["counts"],
                    "pages_scanned": 2,
                },
            },
            change_scan_completed=False,
            created_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=5),
        )
        current = TaskRun(
            task_id="daily",
            status="running",
            triggered_by="manual",
            process_token="token",
            objective="Current",
            scope=daily_scope,
            config_snapshot=snapshot,
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            created_at=now,
            started_at=now,
        )
        session.add_all([prior, latest, other_scope, current])
        session.commit()

        window = _bind_change_window(session, current, task)

        session.refresh(current)
        assert window is not None
        assert (window.start, window.end, window.resume) == (2, 7, True)
        assert current.change_scan_progress["after_watermark"] == 6
        assert current.window_start == prior.window_start
        assert current.window_end == prior.window_end
        assert current.change_scan_completed is False


def test_unfinished_checkpoint_selection_skips_malformed_newer_progress(engine):
    from stocks.execution.config import TaskConfig
    from stocks.execution.service import ExecutionError, _bind_change_window

    seed_task(engine, "daily")
    now = datetime(2026, 10, 21, 6, tzinfo=UTC)
    scope = {"regions": ["US"]}
    fingerprint = scope_fingerprint("daily", scope)
    task = TaskConfig.model_validate(
        {
            "id": "daily",
            "name": "Daily",
            "type": "research",
            "trigger": "manual",
            "objective": "Resume a valid checkpoint.",
            "scope": scope,
            "steps": [
                {
                    "id": "evidence",
                    "select_evidence": {
                        "kind": "select_evidence",
                        "queries": ["inflation"],
                    },
                },
                {
                    "id": "research",
                    "research_agent": {
                        "kind": "research_agent",
                        "agent": "writer",
                        "input": "evidence",
                    },
                },
            ],
        }
    )
    snapshot = {"task": task.model_dump(mode="json"), "agents": []}
    valid_progress = initial_scan_progress(
        ChangeWindow(
            start=0,
            end=5,
            task_id="daily",
            fingerprint=fingerprint,
            resume=False,
        ),
        query_snapshot={
            "queries": ["inflation"],
            "source_ids": ["sec"],
            "limit_per_query": 2,
            "max_tokens": 1000,
            "page_limit": 1,
        },
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(hours=1),
    )
    valid_progress["after_watermark"] = 2
    malformed_progress = deepcopy(valid_progress)
    malformed_progress["after_watermark"] = 99

    def prior_run(objective: str, created_at: datetime, progress: dict) -> TaskRun:
        return TaskRun(
            task_id="daily",
            status="failed",
            triggered_by="manual",
            objective=objective,
            scope=scope,
            config_snapshot=snapshot,
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            change_watermark_start=0,
            change_watermark_end=5,
            window_start=now - timedelta(hours=2),
            window_end=now - timedelta(hours=1),
            scope_fingerprint=fingerprint,
            change_scan_progress=progress,
            change_scan_completed=False,
            created_at=created_at,
            finished_at=created_at + timedelta(minutes=1),
        )

    with Session(engine) as session:
        valid = prior_run(
            "Older valid",
            now - timedelta(minutes=20),
            valid_progress,
        )
        malformed = prior_run(
            "Newer malformed",
            now - timedelta(minutes=10),
            malformed_progress,
        )
        current = TaskRun(
            task_id="daily",
            status="running",
            triggered_by="manual",
            process_token="token",
            objective="Current",
            scope=scope,
            config_snapshot=snapshot,
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            created_at=now,
            started_at=now,
        )
        session.add_all([valid, malformed, current])
        session.commit()

        window = _bind_change_window(session, current, task)

        assert window is not None
        assert (window.start, window.end) == (0, 5)
        assert current.change_scan_progress["after_watermark"] == 2
        current.status = "failed"
        current.process_token = None
        current.finished_at = now
        current.change_scan_progress = {
            **current.change_scan_progress,
            "after_watermark": 99,
        }
        session.commit()

        # When every remaining candidate is malformed, execution fails clearly
        # rather than silently opening a fresh window after the unfinished one.
        valid.change_scan_progress = {
            **valid.change_scan_progress,
            "after_watermark": 99,
        }
        session.commit()
        later = TaskRun(
            task_id="daily",
            status="running",
            triggered_by="manual",
            process_token="token-2",
            objective="Later",
            scope=scope,
            config_snapshot=snapshot,
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            created_at=now + timedelta(minutes=1),
            started_at=now + timedelta(minutes=1),
        )
        session.add(later)
        session.commit()
        with pytest.raises(ExecutionError) as exc_info:
            _bind_change_window(session, later, task)
        assert exc_info.value.code == "SCAN_PROGRESS_INVALID"


def test_cursor_cli_reports_an_absent_cursor_as_null(engine, capsys, monkeypatch):
    import json as json_module

    from stocks.execution import __main__ as cli

    seed_task(engine, "daily")
    monkeypatch.setattr(cli, "session_factory", lambda: lambda: Session(engine))

    assert cli.main(["cursor", "--task", "daily"]) == 0
    payload = json_module.loads(capsys.readouterr().out)
    assert payload["task_id"] == "daily"
    assert payload["cursor"] is None
    assert payload["committed_watermark"] == 0
    assert len(payload["scope_fingerprint"]) == 64


def test_retry_copies_the_source_window_and_scan_position(engine):
    """A retry binds the original ``(start,end]`` rather than a fresh endpoint."""

    from stocks.execution.service import retry_run

    seed_task(engine, "daily")
    now = datetime(2026, 10, 21, 5, tzinfo=UTC)
    with Session(engine) as session:
        source = TaskRun(
            task_id="daily",
            status="failed",
            triggered_by="manual",
            process_token=None,
            objective="Retry",
            strategy=None,
            scope={"regions": ["US"]},
            config_snapshot={
                "task": {
                    "id": "daily",
                    "name": "Daily",
                    "type": "research",
                    "trigger": "manual",
                    "objective": "Retry",
                    "scope": {"regions": ["US"]},
                    "steps": [
                        {
                            "id": "evidence",
                            "select_evidence": {
                                "kind": "select_evidence",
                                "queries": ["inflation"],
                            },
                        },
                        {
                            "id": "research",
                            "research_agent": {
                                "kind": "research_agent",
                                "agent": "writer",
                                "input": "evidence",
                            },
                        },
                    ],
                },
                "agents": [],
            },
            model_snapshot={},
            prompt_snapshot={},
            config_hash="h" * 64,
            change_watermark_start=2,
            change_watermark_end=9,
            window_start=now - timedelta(hours=1),
            window_end=now,
            scope_fingerprint=scope_fingerprint(
                "daily", {"regions": ["US"]}
            ),
            change_scan_progress=initial_scan_progress(
                ChangeWindow(
                    start=2,
                    end=9,
                    task_id="daily",
                    fingerprint=scope_fingerprint(
                        "daily", {"regions": ["US"]}
                    ),
                    resume=False,
                ),
                query_snapshot={
                    "queries": ["inflation"],
                    "source_ids": [],
                    "limit_per_query": 8,
                    "max_tokens": 30000,
                    "page_limit": 100,
                },
                window_start=now - timedelta(hours=1),
                window_end=now,
            ),
            change_scan_completed=False,
            created_at=now,
            started_at=now,
            finished_at=now,
        )
        session.add(source)
        session.commit()
        source_id = source.id

    with Session(engine) as session:
        retried = retry_run(session, source_id)
    assert retried.run.change_watermark_start == 2
    assert retried.run.change_watermark_end == 9
    assert retried.run.change_scan_progress["after_watermark"] == 2
    assert retried.run.window_start is not None
