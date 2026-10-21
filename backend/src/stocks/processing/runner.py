"""Bounded execution of one claimed Document processing stage.

Orchestration lives here, not in the processors: parse reuses the existing
``process_document`` entry point, index reuses ``index_document``, and classify /
facts use a plain callable contract. Registering a real Decision Provider or Fact
extractor belongs to a later task; until then those stages stay visibly pending
with an explicit wait reason instead of being reported as skipped or completed.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.models import (
    Document,
    DocumentParseState,
    DocumentProcessingState,
)
from stocks.processing.stages import (
    OUTCOME_WAITING,
    STAGE_CLASSIFY,
    STAGE_FACTS,
    STAGE_INDEX,
    STAGE_PARSE,
    WAIT_PARSE_UNAVAILABLE,
    WAIT_PROCESSOR_NOT_AVAILABLE,
    ProcessingError,
    StageClaim,
    StageOutcome,
    StageResult,
    apply_stage_outcome,
    claim_stage,
    finish_stage,
    list_stage_states,
    ready_stage_ages,
    reopen_stage,
    view_stage,
)
from stocks.settings import Settings


class StageProcessor(Protocol):
    """Contract for a stage implementation.

    A processor reports only the applicability facts it actually determined. It
    must never claim a stage is irrelevant because of a MIME type, an empty chunk
    list or missing configuration: those are capability gaps expressed as
    :class:`ProcessingError` codes, which keep the stage waiting or failed rather
    than silently skipped.
    """

    version: str

    def __call__(
        self,
        session: Session,
        config: Settings,
        document: Document,
        claim: StageClaim,
    ) -> StageResult | StageOutcome: ...


@dataclass(frozen=True)
class StageRegistry:
    """The processors available in this deployment.

    A ``None`` entry is an unimplemented capability, not an empty result set.
    ``index_factory`` keeps embedding routing lazy: parsing and Run collection
    never read ``config/models.yaml``, and a broken embedding route only fails an
    index attempt that was actually eligible to run.
    """

    parse: StageProcessor | None = None
    classify: StageProcessor | None = None
    facts: StageProcessor | None = None
    index: StageProcessor | None = None
    index_factory: Callable[[], StageProcessor] | None = None

    def available_stages(self) -> frozenset[str]:
        return frozenset(
            stage
            for stage in (STAGE_PARSE, STAGE_CLASSIFY, STAGE_FACTS, STAGE_INDEX)
            if getattr(self, stage) is not None
            or (stage == STAGE_INDEX and self.index_factory is not None)
        )

    def for_stage(self, stage: str) -> StageProcessor | None:
        if stage not in {STAGE_PARSE, STAGE_CLASSIFY, STAGE_FACTS, STAGE_INDEX}:
            raise ProcessingError(
                "UNKNOWN_STAGE", "Unknown Document processing stage"
            )
        processor = getattr(self, stage)
        if processor is None and stage == STAGE_INDEX and self.index_factory is not None:
            return self.index_factory()
        return processor


@dataclass(frozen=True)
class StageExecution:
    """Result of executing one claimed stage, with safe observable detail."""

    document_id: UUID
    stage: str
    status: str
    attempts: int
    max_attempts: int
    error: dict | None = None
    wait_reason: str | None = None
    skip_reason: str | None = None
    processor_version: str | None = None
    input_batch_id: UUID | None = None
    reprocess_requested: bool = False

    def to_dict(self) -> dict:
        result = {
            "document_id": str(self.document_id),
            "stage": self.stage,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "reprocess_requested": self.reprocess_requested,
        }
        if self.error is not None:
            result["error"] = self.error
        if self.wait_reason is not None:
            result["wait_reason"] = self.wait_reason
        if self.skip_reason is not None:
            result["skip_reason"] = self.skip_reason
        if self.processor_version is not None:
            result["processor_version"] = self.processor_version
        if self.input_batch_id is not None:
            result["input_batch_id"] = str(self.input_batch_id)
        return result


def execute_claimed_stage(
    session: Session,
    config: Settings,
    registry: StageRegistry,
    claim: StageClaim,
    *,
    clock: Callable[[], datetime] | None = None,
) -> StageExecution:
    """Run one claimed stage in this Session and persist its outcome.

    A missing processor releases the stage back to ``pending`` with an explicit
    wait reason and consumes no attempt. Any other failure is recorded against
    that stage only, so a failing classification never blocks indexing.
    """

    instant = (clock or _utcnow)()
    document = session.get(Document, claim.document_id)
    if document is None:
        return _persist(
            session,
            StageOutcome(
                document_id=claim.document_id,
                stage=claim.stage,
                owner_token=claim.owner_token,
                status="failed",
                error={
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "Document no longer exists",
                },
                auto_retry=False,
            ),
            instant,
        )

    processor = None
    try:
        processor = registry.for_stage(claim.stage)
    except ProcessingError as exc:
        # A broken or missing capability is a deterministic configuration error:
        # record it once and leave the stage for the Owner instead of burning the
        # automatic retry budget on a setting that cannot fix itself.
        return _persist(
            session,
            StageOutcome(
                document_id=claim.document_id,
                stage=claim.stage,
                owner_token=claim.owner_token,
                status="failed",
                error=exc.to_dict(),
                auto_retry=False,
            ),
            instant,
        )
    if processor is None:
        return _persist(
            session,
            StageOutcome(
                document_id=claim.document_id,
                stage=claim.stage,
                owner_token=claim.owner_token,
                status=OUTCOME_WAITING,
                wait_reason=WAIT_PROCESSOR_NOT_AVAILABLE,
            ),
            instant,
        )

    if claim.stage == STAGE_INDEX:
        # A decision read, so it must not trust a Session-cached pointer: a stale
        # ``current_batch_id`` would mark the claimed stage ``pending`` with
        # ``PARSE_UNAVAILABLE`` even though a current batch exists, and nothing
        # reopens it afterwards. ``populate_existing`` keeps the wait reason tied
        # to the database row that authorises the attempt.
        parse_state = session.scalar(
            select(DocumentParseState)
            .where(DocumentParseState.document_id == document.id)
            .execution_options(populate_existing=True)
        )
        if parse_state is None or parse_state.current_batch_id is None:
            return _persist(
                session,
                StageOutcome(
                    document_id=claim.document_id,
                    stage=claim.stage,
                    owner_token=claim.owner_token,
                    status=OUTCOME_WAITING,
                    wait_reason=WAIT_PARSE_UNAVAILABLE,
                ),
                instant,
            )

    try:
        result = processor(session, config, document, claim)
    except ProcessingError as exc:
        # Discard any partial work this attempt left uncommitted and any aborted
        # transaction, so recording the failure cannot itself fail.
        session.rollback()
        if exc.code == "STAGE_NOT_OWNED":
            # The stage was released while this attempt ran: the Owner recovered it
            # or a new current content batch superseded its input. Its outcome must
            # not be written at all; the durable restart belongs to the stage's
            # current owner, so report the persisted row instead of recording a
            # failure against someone else's attempt.
            return _execution(
                _fresh_stage_state(session, claim.document_id, claim.stage)
            )
        return _persist(
            session,
            StageOutcome(
                document_id=claim.document_id,
                stage=claim.stage,
                owner_token=claim.owner_token,
                status="failed",
                error=exc.to_dict(),
            ),
            instant,
        )
    except Exception:  # noqa: BLE001 - a processor defect is one stage failure
        session.rollback()
        return _persist(
            session,
            StageOutcome(
                document_id=claim.document_id,
                stage=claim.stage,
                owner_token=claim.owner_token,
                status="failed",
                error={
                    "code": "PROCESSOR_FAILED",
                    "message": "Document stage processor did not complete",
                },
            ),
            instant,
        )
    if isinstance(result, StageResult):
        if result.persisted:
            # The processor committed the stage together with its derived result.
            return _execution_from_session(session, claim)
        return _persist(session, result.outcome, instant)
    # A plain callable that returns the outcome directly leaves the write here.
    return _persist(session, result, instant)


def _execution_from_session(
    session: Session, claim: StageClaim
) -> StageExecution:
    return _execution(
        _fresh_stage_state(session, claim.document_id, claim.stage)
    )


def _fresh_stage_state(session: Session, document_id: UUID, stage: str):
    """Read the persisted stage row, bypassing this Session's cached entity."""

    state = session.scalar(
        select(DocumentProcessingState)
        .where(
            DocumentProcessingState.document_id == document_id,
            DocumentProcessingState.stage == stage,
        )
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise ProcessingError(
            "STAGE_NOT_FOUND", "Document has no processing state for this stage"
        )
    return state


def _persist(
    session: Session, outcome: StageOutcome, instant: datetime
) -> StageExecution:
    if outcome.status == OUTCOME_WAITING:
        state = finish_stage(session, outcome, now=instant)
    else:
        state = apply_stage_outcome(session, outcome, now=instant)
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
        session.refresh(state)
    return _execution(state)


def _execution(state) -> StageExecution:
    return StageExecution(
        document_id=state.document_id,
        stage=state.stage,
        status=state.status,
        attempts=state.attempts,
        max_attempts=state.max_attempts,
        error=state.error,
        wait_reason=state.wait_reason,
        skip_reason=state.skip_reason,
        processor_version=state.processor_version,
        input_batch_id=state.input_batch_id,
        reprocess_requested=state.reprocess_requested,
    )


def run_next_stage(
    session_factory,
    config: Settings,
    registry: StageRegistry,
    stage: str,
    *,
    owner_token: str,
    now: datetime | None = None,
) -> StageExecution | None:
    """Claim and execute at most one ready stage of one kind.

    Claiming and executing use separate Sessions: the claim commits before any S3
    read, parse or model call starts, so no row lock is held across network work.
    """

    instant = now or _utcnow()
    with session_factory() as session:
        claim = claim_stage(session, stage, owner_token=owner_token, now=instant)
    if claim is None:
        return None
    with session_factory() as session:
        return execute_claimed_stage(
            session, config, registry, claim, clock=lambda: instant
        )


# Parse unblocks everything else, so it is tried first when the ready Documents
# are equally old. A stage whose processor is not registered in this deployment is
# never claimed, so a missing classifier or Fact extractor is not polled in a loop.
DOCUMENT_STAGE_ORDER = (STAGE_PARSE, STAGE_INDEX, STAGE_CLASSIFY, STAGE_FACTS)


def run_stage_now(
    session_factory,
    config: Settings,
    registry: StageRegistry,
    document_id: UUID,
    stage: str,
    *,
    owner_token: str,
    now: datetime | None = None,
) -> StageExecution | None:
    """Claim and execute one named Document stage through the normal path.

    An explicit Owner command (retry or reprocess) first reopens the stage row and
    then calls this, so it uses the same claim, ownership and result transactions
    as the Worker instead of a parallel execution path. Returns ``None`` when the
    stage is not currently claimable, for example because its dependency is not
    completed.
    """

    instant = now or _utcnow()
    with session_factory() as session:
        claim = claim_stage(
            session,
            stage,
            owner_token=owner_token,
            now=instant,
            document_ids=[document_id],
        )
    if claim is None:
        return None
    with session_factory() as session:
        return execute_claimed_stage(
            session, config, registry, claim, clock=lambda: instant
        )


def run_stages_for_documents(
    session_factory,
    config: Settings,
    registry: StageRegistry,
    document_ids: list[UUID],
    stages: Sequence[str],
    *,
    reprocess: bool = False,
    owner_token: str,
    now: datetime | None = None,
    execute: bool = True,
    confirm_stopped: bool = False,
    completed_is_error: bool = True,
) -> tuple[list[StageExecution], list[dict]]:
    """Explicitly reopen (and optionally execute) stages for chosen Documents.

    Used by the Owner CLIs. Every stage still goes through ``reopen_stage`` and
    the normal claim, so an explicit manual run can never bypass a stage owner or
    write a result outside the stage transaction. ``reprocess=False`` only reopens
    failed or unfinished stages; a ``running`` stage is released only after the
    Owner confirms the old process stopped. With ``execute=False`` the stage is
    only reopened for the Worker, which is what an ordinary retry should do.
    ``completed_is_error=False`` reports an already completed stage as unchanged
    work instead of an error, which is what an ordinary manual parse or index run
    wants.
    """

    instant = now or _utcnow()
    executions: list[StageExecution] = []
    errors: list[dict] = []
    for document_id in document_ids:
        for stage in stages:
            with session_factory() as session:
                try:
                    state = reopen_stage(
                        session,
                        document_id,
                        stage,
                        reprocess=reprocess,
                        now=instant,
                        confirm_stopped=confirm_stopped,
                    )
                except ProcessingError as exc:
                    if (
                        not completed_is_error
                        and exc.code == "STAGE_ALREADY_COMPLETED"
                    ):
                        completed = session.get(
                            DocumentProcessingState, (document_id, stage)
                        )
                        if completed is not None:
                            executions.append(_execution(completed))
                            continue
                    errors.append(
                        {
                            "document_id": str(document_id),
                            "stage": stage,
                            "error": exc.to_dict(),
                        }
                    )
                    continue
                if state.status == "completed":
                    # The interrupted attempt already committed a durable result.
                    executions.append(_execution(state))
                    continue
                reopened = _execution(state)
            if not execute:
                executions.append(reopened)
                continue
            execution = run_stage_now(
                session_factory,
                config,
                registry,
                document_id,
                stage,
                owner_token=owner_token,
                now=instant,
            )
            if execution is None:
                errors.append(
                    {
                        "document_id": str(document_id),
                        "stage": stage,
                        "error": {
                            "code": "STAGE_NOT_CLAIMABLE",
                            "message": (
                                "Document stage is not claimable; its dependency "
                                "is not completed"
                            ),
                        },
                    }
                )
                continue
            executions.append(execution)
    return executions, errors


def describe_stages(
    session: Session,
    registry: StageRegistry,
    *,
    document_ids: list[UUID] | None = None,
    stages: list[str] | None = None,
    statuses: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Read the stage backlog with the deployment's current capability.

    A stage whose processor is not registered is reported as ``pending`` with
    ``wait_reason = PROCESSOR_NOT_AVAILABLE`` even though the Worker never claims
    it: the capability gap has to stay visible instead of looking like an empty
    backlog or a completed stage.
    """

    available = registry.available_stages()
    rows = []
    for state in list_stage_states(
        session,
        document_ids=document_ids,
        stages=stages,
        statuses=statuses,
        limit=limit,
    ):
        view = view_stage(state).to_dict()
        view["processor_available"] = state.stage in available
        if state.status == "pending" and state.stage not in available:
            view["wait_reason"] = WAIT_PROCESSOR_NOT_AVAILABLE
        rows.append(view)
    return rows


def run_next_document_stage(
    session_factory,
    config: Settings,
    registry: StageRegistry,
    *,
    owner_token: str,
    now: datetime | None = None,
) -> StageExecution | None:
    """Claim and execute at most one ready stage across all available stages.

    Returns ``None`` only when no registered stage has ready work, which is what
    lets a document slot wait instead of polling. Among several ready queues the
    oldest ready Document wins, so a steady stream of new Documents cannot starve
    the index work of older ones, and the order stays deterministic.
    """

    instant = now or _utcnow()
    available = registry.available_stages()
    ordered = [stage for stage in DOCUMENT_STAGE_ORDER if stage in available]
    if not ordered:
        return None
    with session_factory() as session:
        ages = ready_stage_ages(session, ordered, now=instant)
    for stage in sorted(ordered, key=lambda item: (ages.get(item, instant), item)):
        if stage not in ages:
            continue
        execution = run_next_stage(
            session_factory,
            config,
            registry,
            stage,
            owner_token=owner_token,
            now=instant,
        )
        if execution is not None:
            return execution
    return None


def _utcnow() -> datetime:
    return datetime.now(UTC)
