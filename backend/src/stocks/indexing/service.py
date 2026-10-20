from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.models import Document, DocumentEmbeddingState, DocumentParseState
from stocks.providers import (
    EmbeddingError,
    EmbeddingResponse,
    EmbeddingTargets,
    ModelClient,
    load_embedding_targets,
)
from stocks.repository import (
    EmbeddingInput,
    get_embedding_state,
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
) -> IndexSummary:
    attempted_at = (clock or (lambda: datetime.now(UTC)))()
    parse_state = session.get(DocumentParseState, document.id)
    if parse_state is None or parse_state.current_batch_id is None:
        error = {
            "code": "PARSED_CHUNKS_UNAVAILABLE",
            "message": "Document has no current successful parse batch",
        }
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
    state = get_embedding_state(session, document.id)
    if (
        not force_reindex
        and state is not None
        and state.status == "completed"
        and state.parse_batch_id == parse_state.current_batch_id
        and state.model_name == targets.model_name
        and state.dimensions == targets.dimensions
        and len(existing_vectors) == len(chunks)
    ):
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


def index_documents(
    session: Session,
    config: Settings,
    targets: EmbeddingTargets,
    client: ModelClient,
    *,
    document_ids: list[UUID] | None = None,
    limit: int = 20,
    force_reindex: bool = False,
    clock: Callable[[], datetime] | None = None,
    embed: Callable[[ModelClient, EmbeddingTargets, list[str]], EmbeddingResponse] = (
        embed_texts
    ),
) -> list[IndexSummary]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if document_ids:
        documents = list(
            session.scalars(
                select(Document).where(Document.id.in_(document_ids))
            ).all()
        )
        by_id = {document.id: document for document in documents}
        missing = [
            document_id
            for document_id in document_ids
            if document_id not in by_id
        ]
        if missing:
            raise ValueError(
                "Unknown document IDs: "
                + ", ".join(str(document_id) for document_id in missing)
            )
        ordered = [by_id[document_id] for document_id in document_ids]
    else:
        ordered = list(
            session.scalars(
                select(Document)
                .order_by(Document.collected_at.desc())
                .limit(limit)
            ).all()
        )
    return [
        index_document(
            session,
            config,
            document,
            targets,
            client,
            force_reindex=force_reindex,
            clock=clock,
            embed=embed,
        )
        for document in ordered
    ]


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
