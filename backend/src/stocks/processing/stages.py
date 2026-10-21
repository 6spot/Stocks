"""Unified background-processing stage state for Document content versions.

One row per ``(document_id, stage)`` records queue/attempt/ownership state only.
Immutable parse batches, the parse-state pointers and embedding coverage stay in
their own tables, so completing a stage never rewrites a stored result and a
failed reprocess never revokes a usable one.

Nothing here calls S3, a parser or a model: claiming is a short transaction, the
processor runs outside it, and the result transaction applies the stage outcome
with the same owner token. A stage left ``running`` by an interrupted process is
never taken over automatically; the Owner confirms the old process stopped and
then calls :func:`recover_stage`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from stocks.models import (
    PROCESSING_STAGES,
    PROCESSING_STAGE_STATUSES,
    Document,
    DocumentEmbeddingState,
    DocumentParseState,
    DocumentProcessingState,
)

STAGE_PARSE = "parse"
STAGE_CLASSIFY = "classify"
STAGE_FACTS = "facts"
STAGE_INDEX = "index"

# A stage may only start once its inputs exist. ``classify`` reads the current
# successful parse batch; ``index`` only needs locatable chunks.
#
# ``facts`` has no hard dependency. Its input is the stored content itself: a
# structured API response is mapped by a Source Mapper from the raw bytes, and a
# text Document is extracted from the current parse batch. Requiring ``parse`` would
# block every JSON/XML response, which has no extractable prose and is therefore
# ``unsupported`` as a parse; requiring ``classify`` would force machine-readable
# payloads through a text classifier. The processor reads the ``should_extract``
# verdict itself and waits while it is undecided, so the applicability relation
# below — not a hard dependency — decides whether Facts is needed.
STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    STAGE_PARSE: (),
    STAGE_CLASSIFY: (STAGE_PARSE,),
    STAGE_FACTS: (),
    STAGE_INDEX: (STAGE_PARSE,),
}

WAIT_PARSE_UNAVAILABLE = "PARSE_UNAVAILABLE"
WAIT_PARSE_UNSUPPORTED = "PARSE_UNSUPPORTED"
WAIT_PROCESSOR_NOT_AVAILABLE = "PROCESSOR_NOT_AVAILABLE"
# Facts is ready to run but the classifier has not produced its should_extract
# verdict yet. Waiting consumes no attempt: the decision is not a failure, it is
# simply not made, and a completed classification clears this reason.
WAIT_CLASSIFICATION_PENDING = "CLASSIFICATION_PENDING"

# Set when a stage is skipped only because a stage it depends on was skipped, so
# it never had its own applicability decided. A later completed dependency reopens
# it; an explicit Owner reprocess of the upstream stage does the same.
SKIP_DEPENDENCY_SKIPPED = "DEPENDENCY_SKIPPED"

# A processor reported the dependent stage is not needed for this content (for
# example a numeric-only response that requires no embedding).
SKIP_NOT_APPLICABLE = "NOT_APPLICABLE"

# Which explicit applicability fact a processor may use to skip a dependent
# stage, keyed by ``(skipping stage, dependent stage)``. Absent pairs are never
# skipped from applicability: the dependent only inherits the skip because its
# own dependency will not produce a result. ``requires_extraction`` is reported by
# the classifier only after it really produced a decision, so a failed or missing
# Decision Model can never be read as "no facts needed".
DIRECT_SKIP_APPLICABILITY: dict[tuple[str, str], str] = {
    (STAGE_PARSE, STAGE_INDEX): "requires_embedding",
    (STAGE_PARSE, STAGE_CLASSIFY): "requires_classification",
    (STAGE_CLASSIFY, STAGE_FACTS): "requires_extraction",
}


def _affected_by(stage: str) -> tuple[str, ...]:
    """Stages whose outcome this stage can decide, in either relation.

    A hard dependency supplies the dependent's input; an applicability pair only
    decides whether the dependent is needed at all. ``classify`` is the second kind
    for ``facts``: the stored content, not the classification, is the input.
    """

    return tuple(
        candidate
        for candidate in PROCESSING_STAGES
        if candidate != stage
        and (
            stage in STAGE_DEPENDENCIES[candidate]
            or (stage, candidate) in DIRECT_SKIP_APPLICABILITY
        )
    )

# Only these stages read the current parse batch and therefore have to run again
# when a new successful batch becomes current. ``facts`` deliberately stays out:
# an explicit reparse of the same Document version cannot invalidate a completed
# extraction, and a new content version is a new Document with its own stages.
PARSE_DEPENDENT_RERUN_STAGES = (STAGE_CLASSIFY, STAGE_INDEX)

OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"
OUTCOME_WAITING = "waiting"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 30
DEFAULT_BACKOFF_MAX_SECONDS = 900
MAX_CLAIM_CANDIDATES = 10


class ProcessingError(Exception):
    """Stable, safe failure carrier for the processing boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class StageClaim:
    """Ownership handed to exactly one executor for one Document stage."""

    document_id: UUID
    stage: str
    owner_token: str
    attempts: int
    max_attempts: int
    reprocess_requested: bool


@dataclass(frozen=True)
class StageOutcome:
    """What one execution attempt produced for a claimed stage.

    ``waiting`` releases the stage back to ``pending`` with an explicit reason and
    does not consume an attempt: a missing processor is a capability gap, not a
    failed run. ``skipped`` requires an explicit applicability basis produced by a
    processor, never an inference from MIME type, empty chunks or missing config.
    """

    document_id: UUID
    stage: str
    owner_token: str
    status: str
    error: dict | None = None
    wait_reason: str | None = None
    skip_reason: str | None = None
    applicability: dict | None = None
    processor_version: str | None = None
    config_fingerprint: str | None = None
    input_batch_id: UUID | None = None
    auto_retry: bool | None = None
    reopen_downstream: bool = False


@dataclass(frozen=True)
class StageResult:
    """What a processor produced for a claimed stage.

    ``persisted`` means the processor already committed the stage outcome in the
    same transaction as its derived result (parse batch, vector batch), so the
    runner must not write the stage again. Everything else is applied by the
    runner in its own transaction.
    """

    outcome: StageOutcome
    persisted: bool = False


@dataclass(frozen=True)
class StageView:
    document_id: UUID
    stage: str
    status: str
    attempts: int
    max_attempts: int
    auto_retry: bool
    reprocess_requested: bool
    next_attempt_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error: dict | None
    wait_reason: str | None
    skip_reason: str | None
    processor_version: str | None
    config_fingerprint: str | None
    input_batch_id: UUID | None
    applicability: dict | None

    def to_dict(self) -> dict:
        return {
            "document_id": str(self.document_id),
            "stage": self.stage,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "auto_retry": self.auto_retry,
            "reprocess_requested": self.reprocess_requested,
            "next_attempt_at": _iso(self.next_attempt_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "error": self.error,
            "wait_reason": self.wait_reason,
            "skip_reason": self.skip_reason,
            "processor_version": self.processor_version,
            "config_fingerprint": self.config_fingerprint,
            "input_batch_id": (
                str(self.input_batch_id) if self.input_batch_id else None
            ),
            "applicability": self.applicability,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def utcnow() -> datetime:
    return datetime.now(UTC)


def view_stage(state: DocumentProcessingState) -> StageView:
    return StageView(
        document_id=state.document_id,
        stage=state.stage,
        status=state.status,
        attempts=state.attempts,
        max_attempts=state.max_attempts,
        auto_retry=state.auto_retry,
        reprocess_requested=state.reprocess_requested,
        next_attempt_at=state.next_attempt_at,
        started_at=state.started_at,
        finished_at=state.finished_at,
        error=state.error,
        wait_reason=state.wait_reason,
        skip_reason=state.skip_reason,
        processor_version=state.processor_version,
        config_fingerprint=state.config_fingerprint,
        input_batch_id=state.input_batch_id,
        applicability=state.applicability,
    )


def create_stage_states(
    session: Session,
    document_id: UUID,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> list[DocumentProcessingState]:
    """Register the four stage rows for one new Document content version.

    Called inside the Document ingest transaction so a committed Document always
    has a durable todo and an uncommitted one never becomes claimable.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    states = [
        DocumentProcessingState(
            document_id=document_id,
            stage=stage,
            status="pending",
            attempts=0,
            max_attempts=max_attempts,
            auto_retry=True,
            reprocess_requested=False,
            next_attempt_at=None,
        )
        for stage in PROCESSING_STAGES
    ]
    session.add_all(states)
    return states


def get_stage_state(
    session: Session, document_id: UUID, stage: str
) -> DocumentProcessingState | None:
    if stage not in PROCESSING_STAGES:
        raise ValueError(f"Unknown processing stage: {stage}")
    return session.get(DocumentProcessingState, (document_id, stage))


def list_stage_states(
    session: Session,
    *,
    document_ids: list[UUID] | None = None,
    stages: list[str] | None = None,
    statuses: list[str] | None = None,
    limit: int = 50,
    dependencies_completed: bool = False,
) -> list[DocumentProcessingState]:
    """Read stage rows with a stable order and a bounded size.

    ``dependencies_completed`` restricts the result to rows that could actually
    run now, for a caller that wants the next actionable work rather than the
    whole backlog. Restricting the query instead of filtering afterwards keeps a
    bounded page from being filled by rows that are not claimable yet.
    """
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if stages:
        unknown = [stage for stage in stages if stage not in PROCESSING_STAGES]
        if unknown:
            raise ValueError("Unknown processing stage: " + ", ".join(unknown))
    if statuses:
        unknown = [
            status for status in statuses if status not in PROCESSING_STAGE_STATUSES
        ]
        if unknown:
            raise ValueError("Unknown processing status: " + ", ".join(unknown))
    statement = (
        select(DocumentProcessingState)
        .join(Document, Document.id == DocumentProcessingState.document_id)
        .order_by(
            Document.collected_at.desc(),
            DocumentProcessingState.document_id,
            DocumentProcessingState.stage,
        )
        .limit(limit)
    )
    if document_ids:
        statement = statement.where(
            DocumentProcessingState.document_id.in_(document_ids)
        )
    if stages:
        statement = statement.where(DocumentProcessingState.stage.in_(stages))
    if statuses:
        statement = statement.where(DocumentProcessingState.status.in_(statuses))
    if dependencies_completed:
        statement = _require_dependencies(statement)
    return list(session.scalars(statement).all())


def _require_dependencies(statement):
    """Restrict a stage query to rows whose dependencies already completed."""

    for stage, dependencies in STAGE_DEPENDENCIES.items():
        if not dependencies:
            continue
        conditions = []
        for dependency in dependencies:
            row = DocumentProcessingState.__table__.alias(f"list_dep_{dependency}")
            conditions.append(
                exists().where(
                    and_(
                        row.c.document_id == DocumentProcessingState.document_id,
                        row.c.stage == dependency,
                        row.c.status == "completed",
                    )
                )
            )
        statement = statement.where(
            (DocumentProcessingState.stage != stage) | and_(*conditions)
        )
    return statement


def _ready_conditions(stage: str, now: datetime):
    retryable = and_(
        DocumentProcessingState.status == "failed",
        DocumentProcessingState.auto_retry.is_(True),
        DocumentProcessingState.attempts < DocumentProcessingState.max_attempts,
    )
    # A capability gap is re-checked only when the deployment actually calls
    # ``claim_stage`` for that stage, i.e. when a processor is registered, so a
    # missing processor is never polled in a loop but also never gets stuck.
    fresh = and_(
        DocumentProcessingState.status == "pending",
        (DocumentProcessingState.wait_reason.is_(None))
        | (
            DocumentProcessingState.wait_reason
            == WAIT_PROCESSOR_NOT_AVAILABLE
        ),
    )
    return [
        DocumentProcessingState.stage == stage,
        (DocumentProcessingState.next_attempt_at.is_(None))
        | (DocumentProcessingState.next_attempt_at <= now),
        retryable | fresh,
    ]


def ready_stage_ages(
    session: Session, stages: Sequence[str], *, now: datetime | None = None
) -> dict[str, datetime]:
    """The oldest ready Document for each requested stage.

    A document slot has to choose among several stage queues. Picking a fixed
    priority would let a steady stream of new Documents starve the later stages of
    older ones, so the caller compares these ages instead: the stage whose oldest
    ready Document was collected first runs next.
    """

    instant = now or utcnow()
    ages: dict[str, datetime] = {}
    for stage in stages:
        if stage not in PROCESSING_STAGES:
            raise ValueError(f"Unknown processing stage: {stage}")
        statement = (
            select(func.min(Document.collected_at))
            .select_from(DocumentProcessingState)
            .join(Document, Document.id == DocumentProcessingState.document_id)
            .where(*_ready_conditions(stage, instant))
        )
        for dependency in STAGE_DEPENDENCIES[stage]:
            dependency_row = DocumentProcessingState.__table__.alias(
                f"age_dep_{dependency}"
            )
            statement = statement.where(
                exists().where(
                    and_(
                        dependency_row.c.document_id
                        == DocumentProcessingState.document_id,
                        dependency_row.c.stage == dependency,
                        dependency_row.c.status == "completed",
                    )
                )
            )
        oldest = session.scalar(statement)
        if oldest is not None:
            ages[stage] = oldest
    session.rollback()
    return ages


def claim_stage(
    session: Session,
    stage: str,
    *,
    owner_token: str,
    now: datetime | None = None,
    max_attempts: int | None = None,
    document_ids: list[UUID] | None = None,
) -> StageClaim | None:
    """Atomically claim one ready Document stage.

    Candidates are narrowed with dependency ``EXISTS`` subqueries, then the
    winning row is locked with ``FOR UPDATE SKIP LOCKED`` and re-checked, so two
    Workers never execute the same stage and a claim never waits on a busy row.
    The claim is committed here; the processor runs outside the transaction.
    ``document_ids`` lets an explicit Owner command address chosen Documents
    through the same ownership path instead of a parallel execution path.
    """

    if stage not in PROCESSING_STAGES:
        raise ValueError(f"Unknown processing stage: {stage}")
    if not owner_token.strip():
        raise ValueError("owner_token must not be empty")
    instant = now or utcnow()
    dependencies = STAGE_DEPENDENCIES[stage]
    candidate_statement = (
        select(DocumentProcessingState.document_id)
        .join(Document, Document.id == DocumentProcessingState.document_id)
        .where(*_ready_conditions(stage, instant))
        .order_by(Document.collected_at, DocumentProcessingState.document_id)
        .limit(MAX_CLAIM_CANDIDATES)
    )
    if document_ids:
        candidate_statement = candidate_statement.where(
            DocumentProcessingState.document_id.in_(document_ids)
        )
    for dependency in dependencies:
        dependency_row = DocumentProcessingState.__table__.alias(
            f"dep_{dependency}"
        )
        candidate_statement = candidate_statement.where(
            exists().where(
                and_(
                    dependency_row.c.document_id
                    == DocumentProcessingState.document_id,
                    dependency_row.c.stage == dependency,
                    dependency_row.c.status == "completed",
                )
            )
        )
    candidates = list(session.scalars(candidate_statement).all())
    if not candidates:
        session.rollback()
        return None

    for document_id in candidates:
        state = session.scalar(
            select(DocumentProcessingState)
            .where(
                DocumentProcessingState.document_id == document_id,
                DocumentProcessingState.stage == stage,
            )
            .with_for_update(skip_locked=True)
            # Re-read the locked row instead of validating a stale cached copy:
            # the candidate list was read before this lock was taken.
            .execution_options(populate_existing=True)
        )
        if state is None:
            continue
        if not _claimable(state, instant):
            session.rollback()
            continue
        if not _dependencies_completed(session, document_id, stage):
            session.rollback()
            continue
        if max_attempts is not None:
            state.max_attempts = max_attempts
        state.status = "running"
        state.owner_token = owner_token
        state.started_at = instant
        state.finished_at = None
        state.next_attempt_at = None
        state.wait_reason = None
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
        return StageClaim(
            document_id=document_id,
            stage=stage,
            owner_token=owner_token,
            attempts=state.attempts,
            max_attempts=state.max_attempts,
            reprocess_requested=state.reprocess_requested,
        )
    session.rollback()
    return None


def _claimable(state: DocumentProcessingState, now: datetime) -> bool:
    if state.next_attempt_at is not None and state.next_attempt_at > now:
        return False
    if state.status == "pending":
        return state.wait_reason in {None, WAIT_PROCESSOR_NOT_AVAILABLE}
    if state.status == "failed":
        return (
            state.auto_retry
            and state.attempts < state.max_attempts
        )
    return False


def _dependencies_completed(
    session: Session, document_id: UUID, stage: str
) -> bool:
    for dependency in STAGE_DEPENDENCIES[stage]:
        state = session.get(DocumentProcessingState, (document_id, dependency))
        if state is None or state.status != "completed":
            return False
    return True


def _lock_owned_state(
    session: Session, document_id: UUID, stage: str, owner_token: str
) -> DocumentProcessingState:
    # ``populate_existing`` is required: sessions run with
    # ``expire_on_commit=False``, so a state entity cached earlier in this
    # execution would otherwise be validated instead of the locked row that may
    # already belong to another owner.
    state = session.scalar(
        select(DocumentProcessingState)
        .where(
            DocumentProcessingState.document_id == document_id,
            DocumentProcessingState.stage == stage,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise ProcessingError(
            "STAGE_NOT_FOUND", "Document has no processing state for this stage"
        )
    if state.status != "running" or state.owner_token != owner_token:
        raise ProcessingError(
            "STAGE_NOT_OWNED",
            "Processing stage is not owned by this execution",
        )
    return state


def apply_stage_outcome(
    session: Session,
    outcome: StageOutcome,
    *,
    now: datetime | None = None,
    max_backoff_seconds: int = DEFAULT_BACKOFF_MAX_SECONDS,
    backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
) -> DocumentProcessingState:
    """Write one attempt's outcome into the stage row without committing.

    Callers that also persist a derived result (a parse batch or a vector batch)
    invoke this inside the same transaction, so a committed result never exists
    without its stage progress and a stale owner can never write either.
    """

    if outcome.status not in {
        OUTCOME_COMPLETED,
        OUTCOME_FAILED,
        OUTCOME_SKIPPED,
        OUTCOME_WAITING,
    }:
        raise ValueError("invalid stage outcome status")
    if outcome.status == OUTCOME_FAILED and outcome.error is None:
        raise ValueError("failed stage outcome must include an error")
    if outcome.status == OUTCOME_SKIPPED and outcome.skip_reason is None:
        raise ValueError("skipped stage outcome must include a skip reason")
    if outcome.status == OUTCOME_WAITING and outcome.wait_reason is None:
        raise ValueError("waiting stage outcome must include a wait reason")
    instant = now or utcnow()
    state = _lock_owned_state(
        session, outcome.document_id, outcome.stage, outcome.owner_token
    )
    state.owner_token = None
    # ``reprocess_requested`` is cleared only by an outcome that actually
    # produced a result. A failed, waiting or interrupted explicit rebuild must
    # keep the request, otherwise its continuation would be a plain retry that
    # can report success from progress the explicit rebuild never finished. See
    # ``OUTCOME_COMPLETED`` / ``OUTCOME_SKIPPED`` below.
    state.wait_reason = None
    state.skip_reason = None
    if outcome.applicability is not None:
        state.applicability = dict(outcome.applicability)
    if outcome.processor_version is not None:
        state.processor_version = outcome.processor_version
    if outcome.config_fingerprint is not None:
        state.config_fingerprint = outcome.config_fingerprint
    if outcome.input_batch_id is not None:
        state.input_batch_id = outcome.input_batch_id

    if outcome.status == OUTCOME_WAITING:
        # A capability gap releases the stage without consuming an attempt.
        state.status = "pending"
        state.wait_reason = outcome.wait_reason
        state.next_attempt_at = None
        state.started_at = None
        state.finished_at = None
        return state

    state.attempts += 1
    state.finished_at = instant
    if outcome.status == OUTCOME_COMPLETED:
        state.status = "completed"
        state.error = None
        state.next_attempt_at = None
        state.reprocess_requested = False
    elif outcome.status == OUTCOME_SKIPPED:
        state.status = "skipped"
        state.skip_reason = outcome.skip_reason
        state.error = None
        state.next_attempt_at = None
        state.reprocess_requested = False
    else:
        state.status = "failed"
        state.error = dict(outcome.error or {})
        if outcome.auto_retry is False:
            state.auto_retry = False
        if state.auto_retry and state.attempts < state.max_attempts:
            delay = min(
                backoff_seconds * (2 ** max(0, state.attempts - 1)),
                max_backoff_seconds,
            )
            state.next_attempt_at = instant + timedelta(seconds=delay)
        else:
            state.next_attempt_at = None
    _apply_downstream(session, state, outcome)
    return state


def _propagate_skips(
    session: Session,
    state: DocumentProcessingState,
    outcome: StageOutcome,
) -> None:
    """Mark dependents skipped from an explicit applicability verdict.

    The only basis is the processor's own ``applicability``: a missing chunk or an
    unsupported MIME type is a capability gap, never proof that nothing needs
    indexing. A stage that *skipped* itself cannot feed the dependents that read
    its result either, so they inherit :data:`SKIP_DEPENDENCY_SKIPPED` instead of
    sitting visibly ``pending`` with no reason; a completed stage still provides
    its result, so nothing is inherited.

    Only unfinished dependents change, so a usable completed result is never
    revoked and a running dependent keeps its own outcome.
    """

    applicability = outcome.applicability or {}
    # Stages whose own result exists for this attempt, and stages that will never
    # produce one. Both stop the search along a dependency chain; only the second
    # is recorded as a skip.
    available = {state.stage} if outcome.status == OUTCOME_COMPLETED else set()
    skipped = {state.stage} if outcome.status == OUTCOME_SKIPPED else set()
    while True:
        progressed = False
        for stage in PROCESSING_STAGES:
            if stage in available or stage in skipped:
                continue
            dependencies = STAGE_DEPENDENCIES[stage]
            field = DIRECT_SKIP_APPLICABILITY.get((state.stage, stage))
            decided_here = field is not None and applicability.get(field) is False
            # A stage is also pointless when a stage that decides its
            # applicability skipped itself: no verdict will ever arrive, so waiting
            # would leave it looking permanently blocked.
            deciding_sources = [
                source
                for (source, dependent) in DIRECT_SKIP_APPLICABILITY
                if dependent == stage
            ]
            dependency_skipped = any(
                dependency in skipped
                for dependency in (*dependencies, *deciding_sources)
            )
            if not decided_here and not dependency_skipped:
                # Its inputs are still pending, so nothing is decided here yet.
                continue
            if decided_here:
                skip_reason = outcome.skip_reason or SKIP_NOT_APPLICABLE
            else:
                skip_reason = SKIP_DEPENDENCY_SKIPPED
            skipped.add(stage)
            progressed = True
            row = session.get(DocumentProcessingState, (state.document_id, stage))
            if row is None or row.status not in {"pending", "failed"}:
                continue
            row.status = "skipped"
            row.skip_reason = skip_reason
            row.wait_reason = None
            row.next_attempt_at = None
            row.owner_token = None
        if not progressed:
            return


def _reopen_dependency_skips(
    session: Session, state: DocumentProcessingState, outcome: StageOutcome
) -> None:
    """Reopen dependents whose skip no longer holds after this completion.

    Two cases reopen:

    * ``SKIP_DEPENDENCY_SKIPPED``: the dependent never had its own applicability
      decided and only waited for this stage's result, which now exists.
    * ``SKIP_NOT_APPLICABLE`` inherited from *this* stage when the new outcome
      explicitly says the dependent is required again. That is how an explicit
      reclassification flipping ``should_extract`` from false to true makes Facts
      runnable again. A completed Fact is never reopened this way, because only a
      ``skipped`` row is touched.
    """

    applicability = outcome.applicability or {}
    for stage in _affected_by(state.stage):
        row = session.get(DocumentProcessingState, (state.document_id, stage))
        if row is None or row.status != "skipped":
            continue
        if row.skip_reason == SKIP_DEPENDENCY_SKIPPED:
            pass
        elif row.skip_reason == SKIP_NOT_APPLICABLE:
            field = DIRECT_SKIP_APPLICABILITY.get((state.stage, stage))
            if field is None or applicability.get(field) is not True:
                continue
        else:
            continue
        row.status = "pending"
        row.attempts = 0
        row.auto_retry = True
        row.error = None
        row.skip_reason = None
        row.applicability = None
        row.next_attempt_at = None
        row.finished_at = None
        row.wait_reason = None


def clear_wait_reason(
    session: Session, document_id: UUID, stage: str, reason: str
) -> None:
    """Drop a waiting reason a stage recorded for itself once it no longer holds.

    Used when the input a stage was waiting for arrives: a ``pending`` row that
    keeps ``CLASSIFICATION_PENDING`` after the classification completed would look
    permanently blocked while actually being claimable.
    """

    row = session.get(DocumentProcessingState, (document_id, stage))
    if row is None or row.status != "pending" or row.wait_reason != reason:
        return
    row.wait_reason = None


def _apply_downstream(
    session: Session,
    state: DocumentProcessingState,
    outcome: StageOutcome,
) -> None:
    """Propagate a stage result to the stages that depend on it.

    Only a genuinely new current content batch reopens finished downstream work.
    A failed reprocess keeps the previous successful batch and therefore leaves
    downstream progress untouched, and a reprocessed classification never reopens
    already extracted facts.
    """

    if outcome.status == OUTCOME_COMPLETED:
        # This stage can now feed the dependents that were only waiting for its
        # result, so an inherited skip no longer applies. Reopening comes first so
        # a fresh applicability verdict below can still skip them again.
        _reopen_dependency_skips(session, state, outcome)
    if outcome.status in {OUTCOME_COMPLETED, OUTCOME_SKIPPED}:
        _propagate_skips(session, state, outcome)
    if outcome.stage == STAGE_CLASSIFY and outcome.status == OUTCOME_COMPLETED:
        # Facts reads the classification verdict itself; a decided verdict ends
        # the wait it recorded while the classifier had not run.
        clear_wait_reason(
            session, state.document_id, STAGE_FACTS, WAIT_CLASSIFICATION_PENDING
        )
    if outcome.stage != STAGE_PARSE:
        return
    dependents = [
        stage
        for stage, dependencies in STAGE_DEPENDENCIES.items()
        if STAGE_PARSE in dependencies
    ]

    if outcome.status == OUTCOME_FAILED:
        current = session.get(DocumentParseState, state.document_id)
        if current is None or current.current_batch_id is None:
            # Derive the waiting reason from the persisted parse status, not from
            # an error-code string: an unsupported MIME type or charset is exactly
            # the case a later retry has to distinguish from a repairable failure.
            reason = (
                WAIT_PARSE_UNSUPPORTED
                if current is not None and current.status == "unsupported"
                else WAIT_PARSE_UNAVAILABLE
            )
            for dependent in dependents:
                row = session.get(
                    DocumentProcessingState, (state.document_id, dependent)
                )
                if row is None or row.status != "pending":
                    continue
                row.wait_reason = reason
                row.next_attempt_at = None
        return

    if outcome.status != OUTCOME_COMPLETED:
        return
    for dependent in dependents:
        if dependent not in PARSE_DEPENDENT_RERUN_STAGES:
            # Facts consume the classification decision, not this parse batch, so
            # a reparse never reopens a completed extraction.
            continue
        row = session.get(DocumentProcessingState, (state.document_id, dependent))
        if row is None:
            continue
        if row.status == "running":
            # A new current batch makes an attempt that is still executing against
            # the previous batch outdated. Release its ownership so the stage is
            # claimable for the new batch; the old owner's writes are refused by
            # the owner-token fence instead of completing the stage for input that
            # is no longer current. The attempt produced no usable result for the
            # new input, so it consumes no retry budget. A stage already running
            # against this same batch keeps its owner.
            if (
                outcome.reopen_downstream
                and row.input_batch_id != outcome.input_batch_id
            ):
                row.status = "pending"
                row.owner_token = None
                row.attempts = 0
                row.auto_retry = True
                row.started_at = None
                row.finished_at = None
                row.error = None
                row.wait_reason = None
                row.skip_reason = None
                row.applicability = None
                row.next_attempt_at = None
                row.input_batch_id = outcome.input_batch_id
            continue
        if outcome.reopen_downstream:
            if row.status in {"completed", "skipped", "failed"}:
                row.status = "pending"
                row.attempts = 0
                row.auto_retry = True
                row.error = None
                row.skip_reason = None
                row.applicability = None
                row.next_attempt_at = None
                row.finished_at = None
            row.wait_reason = None
            row.input_batch_id = outcome.input_batch_id
        elif row.status == "pending":
            row.wait_reason = None
            row.next_attempt_at = None
            row.input_batch_id = outcome.input_batch_id


def finish_stage(
    session: Session,
    outcome: StageOutcome,
    *,
    now: datetime | None = None,
    backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
    max_backoff_seconds: int = DEFAULT_BACKOFF_MAX_SECONDS,
) -> DocumentProcessingState:
    """Apply an outcome and commit it in its own transaction."""

    state = apply_stage_outcome(
        session,
        outcome,
        now=now,
        backoff_seconds=backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)
    return state


def assert_stage_owned(
    session: Session, claim: StageClaim
) -> DocumentProcessingState:
    """Verify this execution still owns the stage, without writing an outcome.

    Used by persistence paths that commit intermediate progress (for example one
    vector batch of a multi-batch index): an interrupted stage may have been
    recovered and claimed by another owner, and the stale owner must not keep
    writing derived results attributed to the current stage.
    """

    return _lock_owned_state(
        session, claim.document_id, claim.stage, claim.owner_token
    )


def retry_stage(
    session: Session,
    document_id: UUID,
    stage: str,
    *,
    now: datetime | None = None,
    confirm_stopped: bool = False,
) -> DocumentProcessingState:
    """Reopen a failed, waiting or interrupted stage for the Owner.

    A ``running`` stage can only be released after the Owner confirms the old
    process stopped; there is no automatic takeover and no lease.
    """

    instant = now or utcnow()
    state = session.scalar(
        select(DocumentProcessingState)
        .where(
            DocumentProcessingState.document_id == document_id,
            DocumentProcessingState.stage == stage,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise ProcessingError(
            "STAGE_NOT_FOUND", "Document has no processing state for this stage"
        )
    if state.status == "running" and not confirm_stopped:
        raise ProcessingError(
            "OWNER_CONFIRMATION_REQUIRED",
            "A running stage can only be released after confirming the old "
            "process stopped",
        )
    if state.status == "completed":
        raise ProcessingError(
            "STAGE_ALREADY_COMPLETED",
            "Use explicit reprocess to run a completed stage again",
        )
    state.status = "pending"
    state.attempts = 0
    state.auto_retry = True
    # A retry reopens failed or unfinished work; it never cancels an outstanding
    # explicit reprocess, which is only satisfied by an attempt that produced a
    # result.
    state.owner_token = None
    state.error = None
    state.wait_reason = None
    state.skip_reason = None
    state.next_attempt_at = None
    state.started_at = None
    state.finished_at = None
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)
    return state


def request_reprocess(
    session: Session,
    document_id: UUID,
    stage: str,
    *,
    now: datetime | None = None,
    confirm_stopped: bool = False,
) -> DocumentProcessingState:
    """Explicitly run a stage again, including one that already completed.

    Reprocessing a classification does not reopen successfully extracted facts,
    and reprocessing the index only reads the existing current chunks.
    """

    instant = now or utcnow()
    state = session.scalar(
        select(DocumentProcessingState)
        .where(
            DocumentProcessingState.document_id == document_id,
            DocumentProcessingState.stage == stage,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise ProcessingError(
            "STAGE_NOT_FOUND", "Document has no processing state for this stage"
        )
    if state.status == "running" and not confirm_stopped:
        raise ProcessingError(
            "OWNER_CONFIRMATION_REQUIRED",
            "A running stage can only be released after confirming the old "
            "process stopped",
        )
    state.status = "pending"
    state.attempts = 0
    state.auto_retry = True
    state.reprocess_requested = True
    state.owner_token = None
    state.error = None
    state.wait_reason = None
    state.skip_reason = None
    state.next_attempt_at = None
    state.started_at = None
    state.finished_at = None
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)
    return state


def recover_stage(
    session: Session,
    document_id: UUID,
    stage: str,
    *,
    now: datetime | None = None,
    confirm_stopped: bool = False,
) -> DocumentProcessingState:
    """Release an interrupted ``running`` stage after the Owner confirms a stop.

    Already committed derived results are kept and recognised as the stage's
    durable result; otherwise the stage returns to ``pending`` with its attempt
    budget intact so a bounded retry continues instead of restarting forever.
    """

    instant = now or utcnow()
    state = session.scalar(
        select(DocumentProcessingState)
        .where(
            DocumentProcessingState.document_id == document_id,
            DocumentProcessingState.stage == stage,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise ProcessingError(
            "STAGE_NOT_FOUND", "Document has no processing state for this stage"
        )
    if state.status != "running":
        raise ProcessingError(
            "STAGE_NOT_RUNNING", "Only an interrupted running stage can be recovered"
        )
    if not confirm_stopped:
        raise ProcessingError(
            "OWNER_CONFIRMATION_REQUIRED",
            "Recovery requires confirming the old process stopped",
        )
    # Decide before mutating: the durable-result lookup queries, and clearing the
    # owner token while still ``running`` would violate the ownership constraint.
    if state.reprocess_requested:
        target = "pending"
    elif _has_durable_result(session, document_id, stage):
        target = "completed"
    else:
        target = "pending"

    state.status = target
    state.owner_token = None
    state.started_at = None
    state.next_attempt_at = None
    state.wait_reason = None
    if target == "completed":
        state.error = None
        state.finished_at = instant
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)
    return state


def _has_durable_result(session: Session, document_id: UUID, stage: str) -> bool:
    if stage == STAGE_PARSE:
        parse_state = session.get(DocumentParseState, document_id)
        return parse_state is not None and parse_state.current_batch_id is not None
    if stage == STAGE_INDEX:
        parse_state = session.get(DocumentParseState, document_id)
        embedding_state = session.get(DocumentEmbeddingState, document_id)
        return (
            parse_state is not None
            and parse_state.current_batch_id is not None
            and embedding_state is not None
            and embedding_state.status == "completed"
            and embedding_state.parse_batch_id == parse_state.current_batch_id
        )
    return False


def reopen_stage(
    session: Session,
    document_id: UUID,
    stage: str,
    *,
    reprocess: bool = False,
    now: datetime | None = None,
    confirm_stopped: bool = False,
) -> DocumentProcessingState:
    """The Owner's single entry point for retrying or reprocessing a stage.

    A ``running`` stage is first released through :func:`recover_stage`, which
    keeps an already committed result instead of redoing it. Only then is the
    requested retry or reprocess applied, so an interrupted stage never loses its
    attempt budget to a blind reset and a finished result is not silently rerun.
    """

    instant = now or utcnow()
    state = get_stage_state(session, document_id, stage)
    if state is None:
        raise ProcessingError(
            "STAGE_NOT_FOUND", "Document has no processing state for this stage"
        )
    if state.status == "running":
        state = recover_stage(
            session,
            document_id,
            stage,
            now=instant,
            confirm_stopped=confirm_stopped,
        )
        if state.status == "completed" and not reprocess:
            # The interrupted attempt had already committed its durable result;
            # recovery recognises it and there is nothing left to retry.
            return state
    if reprocess:
        return request_reprocess(
            session, document_id, stage, now=instant, confirm_stopped=False
        )
    return retry_stage(
        session, document_id, stage, now=instant, confirm_stopped=False
    )
