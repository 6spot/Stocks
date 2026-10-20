from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from stocks.db import session_factory
from stocks.models import Document
from stocks.processing.service import process_documents
from stocks.repository import list_parse_chunks
from stocks.settings import settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse immutable HTML or text Documents into locatable chunks."
    )
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="Document UUID to process; repeat to process multiple documents.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum latest Documents to process when no IDs are supplied (1-200).",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Create a new immutable parse batch instead of reusing a result.",
    )
    parser.add_argument(
        "--include-text",
        action="store_true",
        help="Include chunk text and locators for manual verification.",
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
        with session_factory()() as session:
            summaries = process_documents(
                session,
                settings(),
                document_ids=document_ids,
                limit=args.limit,
                force_reprocess=args.reprocess,
            )
    except Exception:  # noqa: BLE001 - CLI must return only safe stable diagnostics
        print(
            json.dumps(
                {
                    "error": {
                        "code": "PROCESSING_UNAVAILABLE",
                        "message": "Document processing could not be initialized",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2

    items = [item.to_dict() for item in summaries]
    if args.include_text:
        with session_factory()() as session:
            for item, summary in zip(items, summaries):
                document = session.get(Document, summary.document_id)
                chunks = list_parse_chunks(
                    session,
                    summary.document_id,
                    batch_id=summary.batch_id,
                )
                item["document"] = (
                    {
                        "title": document.title,
                        "version": document.version,
                        "completeness": document.completeness,
                        "content_type": document.content_type,
                    }
                    if document is not None
                    else None
                )
                item["chunks"] = [
                    {
                        "id": str(chunk.id),
                        "position": chunk.position,
                        "text": chunk.text,
                        "locator": chunk.locator,
                    }
                    for chunk in chunks
                ]

    output = {
        "processed": len(summaries),
        "completed": sum(item.status == "completed" for item in summaries),
        "failed": sum(item.status == "failed" for item in summaries),
        "unsupported": sum(item.status == "unsupported" for item in summaries),
        "items": items,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if output["failed"] or output["unsupported"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
