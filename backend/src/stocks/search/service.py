from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from stocks.indexing.service import embed_texts
from stocks.providers import EmbeddingResponse, EmbeddingTargets, ModelClient
from stocks.repository import (
    SearchCoverage,
    SearchFilters,
    SearchHit,
    get_embedding_coverage,
    search_chunks,
    search_chunks_exact,
)
from stocks.settings import Settings


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    coverage: SearchCoverage | None
    filters: SearchFilters
    truncated: bool


def semantic_search(
    session: Session,
    config: Settings,
    targets: EmbeddingTargets,
    client: ModelClient,
    query: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 20,
    embed: Callable[[ModelClient, EmbeddingTargets, list[str]], EmbeddingResponse] = (
        embed_texts
    ),
) -> SearchResult:
    normalized = query.strip()
    if not normalized:
        raise ValueError("Search query must not be empty")
    if len(normalized) > config.embedding_max_query_characters:
        raise ValueError("Search query exceeds the configured character limit")
    active_filters = filters or SearchFilters()
    response = embed(client, targets, [normalized])
    if len(response.embeddings) != 1:
        raise ValueError("Embedding provider returned an unexpected query vector")
    hits = search_chunks(
        session,
        response.embeddings[0],
        model_name=targets.model_name,
        dimensions=targets.dimensions,
        filters=active_filters,
        limit=limit,
    )
    coverage = get_embedding_coverage(
        session,
        model_name=targets.model_name,
        dimensions=targets.dimensions,
        filters=active_filters,
    )
    return SearchResult(
        hits=tuple(hits),
        coverage=coverage,
        filters=active_filters,
        truncated=len(hits) == limit,
    )


def exact_search(
    session: Session,
    term: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 20,
    model_name: str | None = None,
    dimensions: int | None = None,
) -> SearchResult:
    active_filters = filters or SearchFilters()
    hits = search_chunks_exact(
        session,
        term,
        filters=active_filters,
        limit=limit,
    )
    coverage = None
    if model_name is not None and dimensions is not None:
        coverage = get_embedding_coverage(
            session,
            model_name=model_name,
            dimensions=dimensions,
            filters=active_filters,
        )
    return SearchResult(
        hits=tuple(hits),
        coverage=coverage,
        filters=active_filters,
        truncated=len(hits) == limit,
    )
