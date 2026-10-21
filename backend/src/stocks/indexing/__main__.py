"""Manual embedding indexing entry point.

Indexing runs through the unified Document stage path: this command only reopens and
executes the ``index`` stage, so it takes the same claim, ownership and result
transaction as the Worker. It never parses, re-reads the original snapshot or
re-extracts Facts; an already completed index is reported as unchanged unless
``--reindex`` explicitly asks for a reprocess.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID, uuid4

from stocks.db import session_factory
from stocks.processing.processors import build_stage_registry
from stocks.processing.runner import run_stages_for_documents
from stocks.processing.stages import STAGE_INDEX, list_stage_states
from stocks.settings import settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create pgvector embeddings for current Document chunks through the "
            "unified Document processing stage path."
        )
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
        help="Maximum Documents to process when no IDs are supplied (1-200).",
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
        with session_factory()() as session:
            targets = (
                list(document_ids)
                if document_ids
                else [
                    state.document_id
                    for state in list_stage_states(
                        session,
                        stages=[STAGE_INDEX],
                        statuses=["pending", "failed"],
                        limit=args.limit,
                        # An index stage is only actionable once its parse
                        # dependency completed; without this filter a backlog of
                        # freshly ingested but unparsed Documents would fill the
                        # whole bounded result and report them all unclaimable.
                        dependencies_completed=True,
                    )
                ]
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

    executions, errors = run_stages_for_documents(
        session_factory(),
        settings(),
        build_stage_registry(args.config),
        targets,
        [STAGE_INDEX],
        reprocess=args.reindex,
        owner_token=f"index-cli-{uuid4().hex}",
        completed_is_error=False,
    )
    items = [item.to_dict() for item in executions]
    output = {
        "processed": len(items),
        "completed": sum(item["status"] == "completed" for item in items),
        "failed": sum(item["status"] == "failed" for item in items),
        "skipped": sum(item["status"] == "skipped" for item in items),
        "items": items,
    }
    if errors:
        output["errors"] = errors
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if output["failed"] or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
