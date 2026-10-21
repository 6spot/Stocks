from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.models import Document, DocumentEmbeddingState, DocumentParseState
from stocks.processing.stages import StageClaim, StageOutcome, apply_stage_outcome
from stocks.providers import (
    EmbeddingError,
    EmbeddingResponse,
    EmbeddingTargets,
    ModelClient,
    load_embedding_targets,
)
from stocks.repository import (
    EmbeddingInput,
    list_chunk_embeddings,
    list_parse_chunks,
    persist_embedding_result,
)
from stocks.settings import Settings


@dataclass(frozen=True)
class IndexSummary:
    document_id: UUID
    version: int
    status: str
    parse_batch_id: UUID | None
    model_name: str
    dimensions: int
    chunk_count: int
    indexed_count: int
    reused: bool
    error: dict | None = None

    def to_dict(self) -> dict:
        result = {
            "document_id": str(self.document_id),
            "version": self.version,
            "status": self.status,
            "parse_batch_id": (
                str(self.parse_batch_id) if self.parse_batch_id else None
            ),
            "model_name": self.model_name,
            "dimensions": self.dimensions,
            "chunk_count": self.chunk_count,
            "indexed_count": self.indexed_count,
            "reused": self.reused,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


def load_embedding_client(
    config_path: str, *, transport=None
) -> tuple[EmbeddingTargets, ModelClient]:
    targets = load_embedding_targets(config_path)
    models = dict(zip(targets.aliases, targets.targets))
    return targets, ModelClient(models, transport=transport)


def embed_texts(
    client: ModelClient,
    targets: EmbeddingTargets,
    texts: list[str],
) -> EmbeddingResponse:
    return asyncio.run(
        client.embed(
            texts,
            aliases=[targets.primary, *targets.fallbacks],
        )
    )


def index_document(
    session: Session,
    config: Settings,
    document: Document,
    targets: EmbeddingTargets,
    client: ModelClient,
    *,
    force_reindex: bool = False,
    clock: Callable[[], datetime] | None = None,
    embed: Callable[[ModelClient, EmbeddingTargets, list[str]], EmbeddingResponse] = (
        embed_texts
    ),
    stage_claim: StageClaim | None = None,
    stage_processor_version: str | None = None,
    stage_config_fingerprint: str | None = None,
) -> IndexSummary:
    attempted_at = (clock or (lambda: datetime.now(UTC)))()
    # Re-read the authoritative parse pointer: this Session keeps entities alive
    # across commits (``expire_on_commit=False``), and an explicit parse reprocess
    # can commit a new current batch while this index attempt is running.
    parse_state = session.scalar(
        select(DocumentParseState)
        .where(DocumentParseState.document_id == document.id)
        .execution_options(populate_existing=True)
    )
    if parse_state is None or parse_state.current_batch_id is None:
        error = {
            "code": "PARSED_CHUNKS_UNAVAILABLE",
            "message": "Document has no current successful parse batch",
        }
        _apply_stage_failure(
            session, stage_claim, error=error, attempted_at=attempted_at
        )
        return IndexSummary(
            document_id=document.id,
            version=document.version,
            status="failed",
            parse_batch_id=None,
            model_name=targets.model_name,
            dimensions=targets.dimensions,
            chunk_count=0,
            indexed_count=0,
            reused=False,
            error=error,
        )

    chunks = list_parse_chunks(session, document.id)
    existing_vectors = list_chunk_embeddings(
        session,
        [chunk.id for chunk in chunks],
        model_name=targets.model_name,
        dimensions=targets.dimensions,
    )
    state = session.scalar(
        select(DocumentEmbeddingState)
        .where(DocumentEmbeddingState.document_id == document.id)
        .execution_options(populate_existing=True)
    )
    if (
        not force_reindex
        and state is not None
        and state.status == "completed"
        and state.parse_batch_id == parse_state.current_batch_id
        and state.model_name == targets.model_name
        and state.dimensions == targets.dimensions
        and len(existing_vectors) == len(chunks)
    ):
        _apply_stage_reuse(
            session,
            stage_claim,
            parse_batch_id=state.parse_batch_id,
            attempted_at=attempted_at,
            processor_version=stage_processor_version,
            config_fingerprint=stage_config_fingerprint,
        )
        return _summarize(
            document,
            status=state.status,
            parse_batch_id=state.parse_batch_id,
            model_name=state.model_name,
            dimensions=state.dimensions,
            chunk_count=state.chunk_count,
            indexed_count=state.indexed_count,
            reused=True,
            error=state.error,
        )

    stale_batch = state is not None and state.parse_batch_id != parse_state.current_batch_id
    pending = (
        chunks
        if force_reindex or stale_batch
        else [chunk for chunk in chunks if chunk.id not in existing_vectors]
    )
    try:
        response_model_alias = targets.primary
        state: DocumentEmbeddingState | None = None
        for offset in range(0, len(pending), config.embedding_batch_size):
            batch = pending[offset : offset + config.embedding_batch_size]
            response = embed(
                client,
                targets,
                [chunk.text for chunk in batch],
            )
            if len(response.embeddings) != len(batch):
                raise EmbeddingError(
                    "EMBEDDING_RESPONSE_INVALID",
                    "Embedding count does not match the current chunk batch",
                )
            response_model_alias = response.model_alias
            embedding_inputs = tuple(
                EmbeddingInput(chunk_id=chunk.id, vector=vector)
                for chunk, vector in zip(batch, response.embeddings)
            )
            is_last_batch = offset + len(batch) == len(pending)
            state = persist_embedding_result(
                session,
                document.id,
                parse_batch_id=parse_state.current_batch_id,
                model_alias=response_model_alias,
                model_name=targets.model_name,
                dimensions=targets.dimensions,
                status="completed" if is_last_batch else "failed",
                embeddings=embedding_inputs,
                attempted_at=attempted_at,
                error=(
                    None
                    if is_last_batch
                    else {
                        "code": "EMBEDDING_INCOMPLETE",
                        "message": "Embedding indexing has not finished",
                    }
                ),
                # Every batch commits vectors under the same owner token; only
                # the final covering batch also commits the stage completion in
                # the same transaction.
                stage_claim=stage_claim,
                stage_final_batch=is_last_batch,
                stage_processor_version=stage_processor_version,
                stage_config_fingerprint=stage_config_fingerprint,
            )
        if state is None:
            state = persist_embedding_result(
                session,
                document.id,
                parse_batch_id=parse_state.current_batch_id,
                model_alias=response_model_alias,
                model_name=targets.model_name,
                dimensions=targets.dimensions,
                status="completed",
                embeddings=(),
                attempted_at=attempted_at,
                error=None,
                stage_claim=stage_claim,
                stage_processor_version=stage_processor_version,
                stage_config_fingerprint=stage_config_fingerprint,
            )
    except EmbeddingError as exc:
        error = exc.to_dict()
        state = persist_embedding_result(
            session,
            document.id,
            parse_batch_id=parse_state.current_batch_id,
            model_alias=targets.primary,
            model_name=targets.model_name,
            dimensions=targets.dimensions,
            status="failed",
            embeddings=(),
            attempted_at=attempted_at,
            error=error,
            stage_claim=stage_claim,
            stage_processor_version=stage_processor_version,
            stage_config_fingerprint=stage_config_fingerprint,
        )
    else:
        error = None

    return _summarize(
        document,
        status=state.status,
        parse_batch_id=state.parse_batch_id,
        model_name=state.model_name,
        dimensions=state.dimensions,
        chunk_count=state.chunk_count,
        indexed_count=state.indexed_count,
        reused=False,
        error=error,
    )


def _summarize(
    document: Document,
    *,
    status: str,
    parse_batch_id: UUID | None,
    model_name: str,
    dimensions: int,
    chunk_count: int,
    indexed_count: int,
    reused: bool,
    error: dict | None,
) -> IndexSummary:
    return IndexSummary(
        document_id=document.id,
        version=document.version,
        status=status,
        parse_batch_id=parse_batch_id,
        model_name=model_name,
        dimensions=dimensions,
        chunk_count=chunk_count,
        indexed_count=indexed_count,
        reused=reused,
        error=error,
    )


def _apply_stage_reuse(
    session: Session,
    stage_claim: StageClaim | None,
    *,
    parse_batch_id: UUID,
    attempted_at: datetime,
    processor_version: str | None = None,
    config_fingerprint: str | None = None,
) -> None:
    """Complete a claimed index stage from already committed vectors."""

    if stage_claim is None:
        return
    state = apply_stage_outcome(
        session,
        StageOutcome(
            document_id=stage_claim.document_id,
            stage=stage_claim.stage,
            owner_token=stage_claim.owner_token,
            status="completed",
            input_batch_id=parse_batch_id,
            processor_version=processor_version,
            config_fingerprint=config_fingerprint,
            applicability={"requires_embedding": True},
        ),
        now=attempted_at,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)


def _apply_stage_failure(
    session: Session,
    stage_claim: StageClaim | None,
    *,
    error: dict,
    attempted_at: datetime,
) -> None:
    """Record a claimed index stage failure when no result could be persisted."""

    if stage_claim is None:
        return
    state = apply_stage_outcome(
        session,
        StageOutcome(
            document_id=stage_claim.document_id,
            stage=stage_claim.stage,
            owner_token=stage_claim.owner_token,
            status="failed",
            error=error,
        ),
        now=attempted_at,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)
