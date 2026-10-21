"""Built-in parse and index stage processors.

These adapt the existing ``process_document`` / ``index_document`` implementations
to the stage contract. They do not reimplement parsing, chunking or embedding, and
they do not commit on their own: the result persistence helpers commit the stage
outcome in the same transaction as the derived result.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.indexing.service import IndexSummary, index_document
from stocks.models import (
    Document,
    DocumentEmbeddingState,
    DocumentParseState,
)
from stocks.processing.parser import PROCESSOR_VERSION as PARSE_PROCESSOR_VERSION
from stocks.processing.service import (
    ParseSummary,
    process_document,
)
from stocks.processing.stages import (
    STAGE_INDEX,
    STAGE_PARSE,
    ProcessingError,
    StageClaim,
    StageOutcome,
    StageResult,
)
from stocks.processing.runner import StageRegistry
from stocks.providers import EmbeddingTargets, ModelClient
from stocks.settings import Settings

# Explicit applicability keys a processor may report. They are the only basis on
# which a downstream stage may be marked skipped; the runner never derives them
# from a MIME type or an empty result.
APPLICABILITY_REQUIRES_EMBEDDING = "requires_embedding"
APPLICABILITY_REQUIRES_CLASSIFICATION = "requires_classification"


def _parse_outcome(claim: StageClaim, summary: ParseSummary) -> StageResult:
    """Wrap the parse result for the runner.

    ``process_document`` already committed the stage outcome in the batch
    transaction, so the runner must not write it a second time.
    """

    return StageResult(
        outcome=StageOutcome(
            document_id=claim.document_id,
            stage=STAGE_PARSE,
            owner_token=claim.owner_token,
            status=(
                "completed" if summary.status == "completed" else "failed"
            ),
            error=summary.error,
            processor_version=summary.processor_version,
            config_fingerprint=summary.config_fingerprint,
            input_batch_id=summary.batch_id,
        ),
        persisted=True,
    )


@dataclass(frozen=True)
class ParseStageProcessor:
    """Parse the stored original into the current immutable chunk batch."""

    version: str = PARSE_PROCESSOR_VERSION
    read_bytes: Callable[..., bytes] | None = None
    clock: Callable[[], datetime] | None = None

    def __call__(
        self,
        session: Session,
        config: Settings,
        document: Document,
        claim: StageClaim,
    ) -> StageResult:
        kwargs: dict = {"clock": self.clock}
        if self.read_bytes is not None:
            kwargs["read_bytes"] = self.read_bytes
        summary = process_document(
            session,
            config,
            document,
            # A claimed retry must really re-run: only an explicit reprocess
            # request forces a new successful batch, but a failed attempt is
            # never reused as a result. The service derives the stage outcome
            # from the actual result, so a failed parse never completes a stage.
            force_reprocess=claim.reprocess_requested,
            stage_claim=claim,
            **kwargs,
        )
        return _parse_outcome(claim, summary)


def index_config_fingerprint(targets: EmbeddingTargets) -> str:
    value = f"{targets.model_name}:{targets.dimensions}"
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class IndexStageProcessor:
    """Generate vectors for the current successful parse batch."""

    targets: EmbeddingTargets
    client: ModelClient
    embed: Callable[..., object] | None = None
    clock: Callable[[], datetime] | None = None

    version: str = "embedding-index-v1"

    def __call__(
        self,
        session: Session,
        config: Settings,
        document: Document,
        claim: StageClaim,
    ) -> StageResult:
        if not claim.reprocess_requested:
            # Read the authoritative rows instead of this Session's cached copies:
            # a parse reprocess can commit a new current batch while an index
            # attempt is running, and the reuse shortcut must never report the
            # old batch as the current index result.
            state = session.scalar(
                select(DocumentEmbeddingState)
                .where(DocumentEmbeddingState.document_id == document.id)
                .execution_options(populate_existing=True)
            )
            parse_state = session.scalar(
                select(DocumentParseState)
                .where(DocumentParseState.document_id == document.id)
                .execution_options(populate_existing=True)
            )
            if (
                state is not None
                and state.status == "completed"
                and parse_state is not None
                and state.parse_batch_id == parse_state.current_batch_id
                and state.model_name == self.targets.model_name
                and state.dimensions == self.targets.dimensions
            ):
                return StageResult(
                    outcome=StageOutcome(
                        document_id=claim.document_id,
                        stage=STAGE_INDEX,
                        owner_token=claim.owner_token,
                        status="completed",
                        processor_version=self.version,
                        config_fingerprint=index_config_fingerprint(self.targets),
                        input_batch_id=state.parse_batch_id,
                        applicability={"requires_embedding": True},
                    )
                )

        kwargs: dict = {"clock": self.clock}
        if self.embed is not None:
            kwargs["embed"] = self.embed
        summary = index_document(
            session,
            config,
            document,
            self.targets,
            self.client,
            force_reindex=claim.reprocess_requested,
            # ``index_document`` derives the stage status from the coverage it
            # actually persisted, so a partial run never completes the stage.
            stage_claim=claim,
            stage_processor_version=self.version,
            stage_config_fingerprint=index_config_fingerprint(self.targets),
            **kwargs,
        )
        return _index_outcome(claim, summary)


def _index_outcome(claim: StageClaim, summary: IndexSummary) -> StageResult:
    """Wrap the index result; ``index_document`` committed the stage already."""

    return StageResult(
        outcome=StageOutcome(
            document_id=claim.document_id,
            stage=STAGE_INDEX,
            owner_token=claim.owner_token,
            status=(
                "completed" if summary.status == "completed" else "failed"
            ),
            error=summary.error,
            processor_version="embedding-index-v1",
            input_batch_id=summary.parse_batch_id,
        ),
        persisted=True,
    )

def build_parse_processor(
    *,
    read_bytes: Callable[..., bytes] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ParseStageProcessor:
    return ParseStageProcessor(read_bytes=read_bytes, clock=clock)


def build_index_processor(
    targets: EmbeddingTargets,
    client: ModelClient,
    *,
    embed: Callable[..., object] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> IndexStageProcessor:
    return IndexStageProcessor(
        targets=targets, client=client, embed=embed, clock=clock
    )


def load_index_processor(
    model_config_path: str,
    *,
    model_transport=None,
    embed: Callable[..., object] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> IndexStageProcessor:
    """Build the index processor from the configured embedding route.

    Called only when an eligible index stage is actually claimed, so parsing and
    Run collection never read the embedding configuration and a broken route can
    never stop them.
    """

    from stocks.indexing.service import load_embedding_client

    try:
        targets, client = load_embedding_client(
            model_config_path, transport=model_transport
        )
    except (ValueError, OSError) as exc:
        raise ProcessingError(
            "EMBEDDING_ROUTING_UNAVAILABLE",
            "Embedding routing is not usable for indexing",
        ) from exc
    return build_index_processor(targets, client, embed=embed, clock=clock)


def build_stage_registry(
    model_config_path: str,
    *,
    read_bytes: Callable[..., bytes] | None = None,
    model_transport=None,
    embed: Callable[..., object] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> StageRegistry:
    """The processors this deployment can run.

    Parse and index are wired to the existing implementations; classify and facts
    stay unregistered until their implementations land, so their stages remain
    visibly pending with ``PROCESSOR_NOT_AVAILABLE`` instead of being reported as
    completed, skipped or irrelevant. The embedding route is resolved lazily by
    ``index_factory`` for the same reason.
    """

    return StageRegistry(
        parse=build_parse_processor(read_bytes=read_bytes, clock=clock),
        index_factory=lambda: load_index_processor(
            model_config_path,
            model_transport=model_transport,
            embed=embed,
            clock=clock,
        ),
    )
