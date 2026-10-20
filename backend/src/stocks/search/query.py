from __future__ import annotations

from uuid import UUID

from stocks.indexing.service import load_embedding_client
from stocks.providers import EmbeddingTargets, ModelClient
from stocks.repository import SearchFilters, SearchHit, get_chunk_context
from stocks.search.service import SearchResult, semantic_search
from stocks.settings import Settings


def search_repository(
    session,
    config: Settings,
    config_path: str,
    query: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 20,
    targets: EmbeddingTargets | None = None,
    client: ModelClient | None = None,
) -> SearchResult:
    if targets is None or client is None:
        targets, client = load_embedding_client(config_path)
    return semantic_search(
        session,
        config,
        targets,
        client,
        query,
        filters=filters,
        limit=limit,
    )


def hits_to_dicts(hits: tuple[SearchHit, ...]) -> list[dict]:
    return [
        {
            "chunk_id": str(hit.chunk_id),
            "document_id": str(hit.document_id),
            "parse_batch_id": str(hit.parse_batch_id),
            "position": hit.position,
            "text": hit.text,
            "locator": hit.locator,
            "title": hit.title,
            "source_id": hit.source_id,
            "version": hit.version,
            "published_at": (
                hit.published_at.isoformat() if hit.published_at else None
            ),
            "source_updated_at": (
                hit.source_updated_at.isoformat()
                if hit.source_updated_at
                else None
            ),
            "collected_at": hit.collected_at.isoformat(),
            "completeness": hit.completeness,
            "similarity": hit.similarity,
        }
        for hit in hits
    ]


def context_search(
    session,
    chunk_id: str,
    *,
    radius: int = 1,
) -> SearchResult:
    try:
        normalized_id = UUID(chunk_id)
    except ValueError as exc:
        raise ValueError("Chunk ID must be a valid UUID") from exc
    hits = tuple(
        get_chunk_context(session, normalized_id, radius=radius)
    )
    return SearchResult(
        hits=hits,
        coverage=None,
        filters=SearchFilters(),
        truncated=False,
    )
