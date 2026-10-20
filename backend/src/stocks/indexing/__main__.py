from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from stocks.db import session_factory
from stocks.indexing.service import index_documents, load_embedding_client
from stocks.settings import settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create pgvector embeddings for current Document chunks."
    )
    parser.add_argument(
        "--config",
        default="config/models.yaml",
        help="Provider/model YAML configuration path.",
    )
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="Document UUID to index; repeat to index multiple documents.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum latest Documents to process when no IDs are supplied (1-200).",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Regenerate vectors for the current chunk batch.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")
    try:
        document_ids = (
            [UUID(value) for value in args.document_ids]
            if args.document_ids
            else None
        )
    except ValueError:
        parser.error("--document-id must be a valid UUID")

    try:
        targets, client = load_embedding_client(args.config)
        with session_factory()() as session:
            summaries = index_documents(
                session,
                settings(),
                targets,
                client,
                document_ids=document_ids,
                limit=args.limit,
                force_reindex=args.reindex,
            )
    except Exception:  # noqa: BLE001 - CLI must return only safe diagnostics
        print(
            json.dumps(
                {
                    "error": {
                        "code": "INDEXING_UNAVAILABLE",
                        "message": "Embedding indexing could not be initialized",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2

    items = [item.to_dict() for item in summaries]
    output = {
        "processed": len(summaries),
        "completed": sum(item.status == "completed" for item in summaries),
        "failed": sum(item.status == "failed" for item in summaries),
        "reused": sum(item.reused for item in summaries),
        "items": items,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if output["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
