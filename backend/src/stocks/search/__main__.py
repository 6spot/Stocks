from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime

from stocks.db import session_factory
from stocks.indexing.service import load_embedding_client
from stocks.repository import SearchFilters
from stocks.search.query import context_search, hits_to_dicts
from stocks.search.service import exact_search, semantic_search
from stocks.settings import settings


def _timezone_aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must be ISO 8601") from exc
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Search current Document chunks by pgvector or short exact code."
    )
    parser.add_argument("--query", help="Natural-language semantic search query.")
    parser.add_argument("--exact", help="Bounded exact stock code or identifier.")
    parser.add_argument(
        "--chunk-id",
        help="Read the current stored Chunk and adjacent context.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        help="Adjacent Chunk radius for --chunk-id (0-5; default 1).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Provider/model YAML configuration path (default: Settings).",
    )
    parser.add_argument("--source-id", action="append", dest="source_ids")
    parser.add_argument("--published-from", type=_timezone_aware)
    parser.add_argument("--published-to", type=_timezone_aware)
    parser.add_argument("--collected-from", type=_timezone_aware)
    parser.add_argument("--collected-to", type=_timezone_aware)
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum semantic or exact hits (1-200; default 20).",
    )
    args = parser.parse_args(argv)
    modes = sum(bool(value) for value in (args.query, args.exact, args.chunk_id))
    if modes != 1:
        parser.error("choose exactly one of --query, --exact or --chunk-id")
    if args.radius is not None and not 0 <= args.radius <= 5:
        parser.error("--radius must be between 0 and 5")
    if args.limit is not None and not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")
    if args.chunk_id and any(
        (
            args.source_ids,
            args.published_from,
            args.published_to,
            args.collected_from,
            args.collected_to,
        )
    ):
        parser.error("source and time filters are only valid with --query or --exact")
    if args.chunk_id and args.limit is not None:
        parser.error("--limit is only valid with --query or --exact")
    if not args.chunk_id and args.radius is not None:
        parser.error("--radius is only valid with --chunk-id")

    limit = args.limit if args.limit is not None else 20
    radius = args.radius if args.radius is not None else 1

    try:
        filters = SearchFilters(
            source_ids=tuple(args.source_ids or ()),
            published_from=args.published_from,
            published_to=args.published_to,
            collected_from=args.collected_from,
            collected_to=args.collected_to,
        )
        with session_factory()() as session:
            if args.query:
                targets, client = load_embedding_client(
                    args.config
                    if args.config is not None
                    else settings().models_config_path
                )
                result = semantic_search(
                    session,
                    settings(),
                    targets,
                    client,
                    args.query,
                    filters=filters,
                    limit=limit,
                )
            elif args.exact:
                result = exact_search(
                    session,
                    args.exact,
                    filters=filters,
                    limit=limit,
                )
            else:
                result = context_search(
                    session,
                    args.chunk_id,
                    radius=radius,
                )
    except Exception:  # noqa: BLE001 - CLI must return only safe diagnostics
        print(
            json.dumps(
                {
                    "error": {
                        "code": "SEARCH_UNAVAILABLE",
                        "message": "Evidence search could not be completed",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2

    coverage = result.coverage
    coverage_payload = (
        {
            "current_documents": coverage.current_documents,
            "indexed_documents": coverage.indexed_documents,
            "failed_documents": coverage.failed_documents,
            "documents_without_complete_index": (
                coverage.documents_without_complete_index
            ),
            "current_chunks": coverage.current_chunks,
            "indexed_chunks": coverage.indexed_chunks,
            "unindexed_chunks": coverage.unindexed_chunks,
        }
        if coverage is not None
        else None
    )
    output = {
        "mode": (
            "semantic"
            if args.query
            else "exact"
            if args.exact
            else "context"
        ),
        "query": args.query or args.exact or args.chunk_id,
        "filters": {
            "source_ids": list(result.filters.source_ids),
            "published_from": (
                result.filters.published_from.isoformat()
                if result.filters.published_from
                else None
            ),
            "published_to": (
                result.filters.published_to.isoformat()
                if result.filters.published_to
                else None
            ),
            "collected_from": (
                result.filters.collected_from.isoformat()
                if result.filters.collected_from
                else None
            ),
            "collected_to": (
                result.filters.collected_to.isoformat()
                if result.filters.collected_to
                else None
            ),
        },
        "truncated": result.truncated,
        "coverage": coverage_payload,
        "hits": hits_to_dicts(result.hits),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
