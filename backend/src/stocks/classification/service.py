"""Lightweight Document classification on the existing processing stages.

One classification attempt reads the current successful parse batch under a
character budget, makes one Decision Model call, validates the answer against the
Stocks-controlled vocabulary and writes the result into the Document's current
``classification`` JSONB. It creates no Document, no classification history table
and no Facts: ``should_extract`` is a decision consumed by the ``facts`` stage,
which belongs to another task.

Failure semantics follow the stage contract: an unusable answer, a provider
failure or a missing route is a diagnosable failure with a safe error code. It is
never recorded as ``should_extract=false`` and never removes the original
Document, its Chunks or its vectors.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.decision import (
    DECISION_AGENT,
    ClassificationDecision,
    ClassificationInput,
    DecisionError,
    DecisionRoute,
    build_classification_input,
    classify_document,
    decision_client,
    load_decision_route,
)
from stocks.models import Document, DocumentParseState
from stocks.processing.processors import APPLICABILITY_REQUIRES_EXTRACTION
from stocks.processing.stages import (
    STAGE_CLASSIFY,
    ProcessingError,
    StageClaim,
    StageOutcome,
    StageResult,
    apply_stage_outcome,
    assert_stage_owned,
)
from stocks.repository import list_parse_chunks
from stocks.settings import Settings
from stocks.taxonomy import Taxonomy, load_taxonomy

CLASSIFIER_PROCESSOR_VERSION = "decision-model-v1"
METHOD = "decision_model"
DEFAULT_TAXONOMY_PATH = "config/taxonomy.yaml"


@dataclass(frozen=True)
class ClassificationSummary:
    document_id: UUID
    version: int
    status: str
    category: str | None
    should_extract: bool | None
    taxonomy_version: str | None
    model_ref: str | None
    parse_batch_id: UUID | None
    input_characters: int
    truncated: bool
    error: dict | None = None

    def to_dict(self) -> dict:
        result = {
            "document_id": str(self.document_id),
            "version": self.version,
            "status": self.status,
            "category": self.category,
            "should_extract": self.should_extract,
            "taxonomy_version": self.taxonomy_version,
            "model_ref": self.model_ref,
            "parse_batch_id": (
                str(self.parse_batch_id) if self.parse_batch_id else None
            ),
            "input_characters": self.input_characters,
            "truncated": self.truncated,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


def taxonomy_fingerprint(taxonomy: Taxonomy) -> str:
    """Identity of the vocabulary that produced a stored classification."""

    payload = (
        f"{taxonomy.version}:"
        f"{','.join(taxonomy.category_ids)}:"
        f"{','.join(taxonomy.event_type_ids)}:"
        f"{','.join(taxonomy.topic_ids)}:"
        f"{','.join(taxonomy.industry_ids)}:"
        f"{','.join(taxonomy.region_ids)}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_config_fingerprint(route: DecisionRoute, taxonomy: Taxonomy) -> str:
    """Processor identity: protocol, model route and vocabulary — no credential."""

    value = (
        f"{CLASSIFIER_PROCESSOR_VERSION}:"
        f"{route.protocol}:"
        f"{route.routing.primary}:"
        f"{','.join(route.routing.fallbacks)}:"
        f"{taxonomy_fingerprint(taxonomy)}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_classifier_route(
    model_config_path: str, *, transport=None
) -> tuple[DecisionRoute, object]:
    """Resolve the Decision Model route and its client.

    Called only when a classify stage is actually claimed, so parsing, indexing and
    Run collection never read the decision route and a broken decision
    configuration only fails a classify attempt.
    """

    try:
        route = load_decision_route(model_config_path)
    except (ValueError, OSError) as exc:
        raise ProcessingError(
            "DECISION_ROUTING_UNAVAILABLE",
            "Decision Model routing is not usable for classification",
        ) from exc
    return route, decision_client(route, transport=transport)


def load_taxonomy_or_fail(path: str = DEFAULT_TAXONOMY_PATH) -> Taxonomy:
    try:
        return load_taxonomy(path)
    except (ValueError, OSError) as exc:
        raise ProcessingError(
            "TAXONOMY_UNAVAILABLE",
            "Classification vocabulary is not usable",
        ) from exc


@dataclass(frozen=True)
class ClassifyStageProcessor:
    """The registered ``classify`` stage processor."""

    route: DecisionRoute
    client: object
    taxonomy: Taxonomy
    version: str = CLASSIFIER_PROCESSOR_VERSION

    def __call__(
        self,
        session: Session,
        config: Settings,
        document: Document,
        claim: StageClaim,
    ) -> StageResult:
        summary = classify_document_stage(
            session,
            config,
            document,
            route=self.route,
            client=self.client,
            taxonomy=self.taxonomy,
            claim=claim,
        )
        # The service committed the classification together with the stage
        # outcome, so the runner must not write the stage a second time.
        return StageResult(
            outcome=StageOutcome(
                document_id=claim.document_id,
                stage=STAGE_CLASSIFY,
                owner_token=claim.owner_token,
                status=summary.status,
                error=summary.error,
            ),
            persisted=True,
        )


def classify_document_stage(
    session: Session,
    config: Settings,
    document: Document,
    *,
    route: DecisionRoute,
    client: object,
    taxonomy: Taxonomy,
    claim: StageClaim | None = None,
    max_characters: int | None = None,
    clock: Callable[[], datetime] | None = None,
    classify: Callable[..., ClassificationDecision] = classify_document,
) -> ClassificationSummary:
    """Classify one Document version and persist the current result.

    The Document row is locked and the parse pointer is re-read from the database,
    so a classification that started against an older batch cannot overwrite the
    result of a newer current batch: the stage has already been superseded, and
    ``apply_stage_outcome`` refuses the stale owner token.
    """

    attempted_at = (clock or (lambda: datetime.now(UTC)))()
    budget = max_characters or config.classification_max_characters

    locked = session.scalar(
        select(Document).where(Document.id == document.id).with_for_update()
    )
    if locked is None:
        raise ValueError(f"Unknown document: {document.id}")
    parse_state = session.scalar(
        select(DocumentParseState)
        .where(DocumentParseState.document_id == document.id)
        .execution_options(populate_existing=True)
    )
    if parse_state is None or parse_state.current_batch_id is None:
        return _persist_failure(
            session,
            locked,
            claim,
            {
                "code": "PARSED_CHUNKS_UNAVAILABLE",
                "message": "Document has no current successful parse batch",
            },
            attempted_at,
        )
    batch_id = parse_state.current_batch_id
    chunks = list_parse_chunks(session, document.id, batch_id=batch_id)
    if not chunks:
        return _persist_failure(
            session,
            locked,
            claim,
            {
                "code": "PARSED_CHUNKS_UNAVAILABLE",
                "message": "Current parse batch contains no text to classify",
            },
            attempted_at,
            parse_batch_id=batch_id,
        )

    classification_input = build_classification_input(
        locked, chunks, max_characters=budget
    )
    if not classification_input.text.strip():
        return _persist_failure(
            session,
            locked,
            claim,
            {
                "code": "CLASSIFICATION_INPUT_EMPTY",
                "message": "Selected excerpt contains no text to classify",
            },
            attempted_at,
            parse_batch_id=batch_id,
            classification_input=classification_input,
        )

    try:
        decision = classify(route, client, taxonomy, classification_input)
    except DecisionError as exc:
        return _persist_failure(
            session,
            locked,
            claim,
            exc.to_dict(),
            attempted_at,
            parse_batch_id=batch_id,
            classification_input=classification_input,
        )
    except Exception:  # noqa: BLE001 - a provider defect is one stage failure
        return _persist_failure(
            session,
            locked,
            claim,
            {
                "code": "CLASSIFICATION_FAILED",
                "message": "Decision Model did not complete",
            },
            attempted_at,
            parse_batch_id=batch_id,
            classification_input=classification_input,
        )

    stored = {
        **deepcopy(decision.result),
        "method": METHOD,
        "model_ref": DECISION_AGENT,
        "model_alias": decision.model_alias,
        "requested_model_name": decision.requested_model_name,
        "reported_model_name": decision.reported_model_name,
        "taxonomy_version": taxonomy.version,
        "taxonomy_fingerprint": taxonomy_fingerprint(taxonomy),
        "classified_at": attempted_at.isoformat(),
        "reason_or_evidence_ref": {
            "reason": decision.result["reason"],
            "parse_batch_id": str(batch_id),
            "chunk_ids": list(classification_input.chunk_ids),
            "input_characters": len(classification_input.text),
            "truncated": classification_input.truncated,
        },
        "usage": dict(decision.usage),
    }
    locked.classification = stored
    if claim is not None:
        # Validate ownership before anything is applied, so a superseded attempt
        # cannot even stage a write that a later commit in this transaction would
        # flush. ``apply_stage_outcome`` re-checks under the same row lock.
        assert_stage_owned(session, claim)
        apply_stage_outcome(
            session,
            StageOutcome(
                document_id=locked.id,
                stage=STAGE_CLASSIFY,
                owner_token=claim.owner_token,
                status="completed",
                processor_version=CLASSIFIER_PROCESSOR_VERSION,
                config_fingerprint=classify_config_fingerprint(route, taxonomy),
                input_batch_id=batch_id,
                applicability={
                    # An explicit decision: the classifier determined whether this
                    # content requires structured extraction.
                    APPLICABILITY_REQUIRES_EXTRACTION: bool(
                        decision.result["should_extract"]
                    ),
                },
            ),
            now=attempted_at,
        )
    _commit(session)
    session.refresh(locked)
    return _summary(
        locked,
        stored,
        status="completed",
        parse_batch_id=batch_id,
        classification_input=classification_input,
    )


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def _persist_failure(
    session: Session,
    document: Document,
    claim: StageClaim | None,
    error: dict,
    attempted_at: datetime,
    *,
    parse_batch_id: UUID | None = None,
    classification_input: ClassificationInput | None = None,
) -> ClassificationSummary:
    """Record a failed attempt without touching the stored classification.

    A previous successful classification stays readable: a failed retry is a
    processing state, not a reason to erase a result research may already cite.
    """

    if claim is not None:
        apply_stage_outcome(
            session,
            StageOutcome(
                document_id=document.id,
                stage=STAGE_CLASSIFY,
                owner_token=claim.owner_token,
                status="failed",
                error=error,
                processor_version=CLASSIFIER_PROCESSOR_VERSION,
                input_batch_id=parse_batch_id,
            ),
            now=attempted_at,
        )
    _commit(session)
    session.refresh(document)
    return _summary(
        document,
        document.classification or {},
        status="failed",
        parse_batch_id=parse_batch_id,
        classification_input=classification_input,
        error=error,
    )


def _summary(
    document: Document,
    stored: dict,
    *,
    status: str,
    parse_batch_id: UUID | None,
    classification_input: ClassificationInput | None,
    error: dict | None = None,
) -> ClassificationSummary:
    reference = stored.get("reason_or_evidence_ref") or {}
    return ClassificationSummary(
        document_id=document.id,
        version=document.version,
        status=status,
        category=stored.get("category"),
        should_extract=stored.get("should_extract"),
        taxonomy_version=stored.get("taxonomy_version"),
        model_ref=stored.get("model_ref"),
        parse_batch_id=parse_batch_id,
        input_characters=(
            len(classification_input.text)
            if classification_input is not None
            else int(reference.get("input_characters") or 0)
        ),
        truncated=(
            classification_input.truncated
            if classification_input is not None
            else bool(reference.get("truncated", False))
        ),
        error=error,
    )


def load_classify_processor(
    model_config_path: str,
    *,
    model_transport=None,
    taxonomy_path: str = DEFAULT_TAXONOMY_PATH,
) -> ClassifyStageProcessor:
    """Build the classify processor from the configured decision route.

    Both the route and the vocabulary are resolved lazily, only when an eligible
    classify stage is claimed, so a broken decision configuration never stops
    parsing or indexing.
    """

    route, client = load_classifier_route(
        model_config_path, transport=model_transport
    )
    taxonomy = load_taxonomy_or_fail(taxonomy_path)
    return ClassifyStageProcessor(route=route, client=client, taxonomy=taxonomy)


def describe_classification(document: Document) -> dict | None:
    """The stored current classification, or ``None`` when it never succeeded."""

    if document.classification is None:
        return None
    return deepcopy(document.classification)


__all__ = [
    "CLASSIFIER_PROCESSOR_VERSION",
    "ClassificationSummary",
    "ClassifyStageProcessor",
    "classify_config_fingerprint",
    "classify_document_stage",
    "describe_classification",
    "load_classify_processor",
    "load_classifier_route",
    "load_taxonomy_or_fail",
    "taxonomy_fingerprint",
]
