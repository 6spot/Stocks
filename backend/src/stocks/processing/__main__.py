"""Unified Document background-processing CLI.

One command surface owns the parse/classify/facts/index backlog: it queries the
persisted stage rows, retries failed or unfinished work, explicitly reprocesses a
completed stage and runs an explicit manual parse or index. Every execution goes
through the same claim, ownership and result transactions as the Worker, so no
manual command can bypass a stage owner or write a result outside the stage
transaction.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID, uuid4

from stocks.db import session_factory
from stocks.models import PROCESSING_STAGES, PROCESSING_STAGE_STATUSES, Document
from stocks.processing.processors import build_stage_registry
from stocks.processing.runner import (
    describe_stages,
    run_stages_for_documents,
)
from stocks.processing.stages import (
    STAGE_FACTS,
    STAGE_INDEX,
    STAGE_PARSE,
    list_stage_states,
)
from stocks.repository import list_parse_chunks
from stocks.settings import settings

DEFAULT_MODEL_CONFIG = "config/models.yaml"
DEFAULT_SOURCES_CONFIG = "config/sources.yaml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Query and recover the unified Document background-processing backlog "
            "(parse, classify, facts, index)."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser(
        "status", help="List Document stage states and this deployment's capability."
    )
    _add_filters(status)
    status.add_argument(
        "--limit", type=int, default=50, help="Maximum stage rows to return (1-200)."
    )

    retry = subparsers.add_parser(
        "retry",
        help="Reopen a failed or unfinished stage so it runs again.",
    )
    _add_reopen_arguments(retry)

    reprocess = subparsers.add_parser(
        "reprocess",
        help="Explicitly run a stage again, including one that already completed.",
    )
    _add_reopen_arguments(reprocess)

    parse = subparsers.add_parser(
        "parse", help="Parse selected Documents through the parse stage."
    )
    _add_execute_arguments(parse)
    parse.add_argument(
        "--include-text",
        action="store_true",
        help="Include chunk text and locators for manual verification.",
    )

    index = subparsers.add_parser(
        "index", help="Index selected Documents through the index stage."
    )
    _add_execute_arguments(index)

    facts = subparsers.add_parser(
        "facts",
        help="Extract or map Facts for selected Documents through the facts stage.",
    )
    _add_execute_arguments(facts)
    facts.add_argument(
        "--source-id",
        help="Report the stored Facts of one source instead of processing anything.",
    )
    facts.add_argument(
        "--document-id-report",
        action="append",
        dest="report_document_ids",
        help="Report the stored Facts of these Documents; repeat for several.",
    )
    return parser


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="Only report these Document UUIDs; repeat for several.",
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=list(PROCESSING_STAGES),
        help="Only report these stages; repeat for several.",
    )
    parser.add_argument(
        "--status",
        action="append",
        choices=list(PROCESSING_STAGE_STATUSES),
        help="Only report these statuses; repeat for several.",
    )


def _add_reopen_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--document-id", action="append", dest="document_ids", required=True
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=list(PROCESSING_STAGES),
        required=True,
        help="Stage to reopen; repeat to address several stages.",
    )
    parser.add_argument(
        "--confirm-stopped",
        action="store_true",
        help=(
            "Confirm the process that owned a running stage has stopped. Required "
            "to release a running stage; the Worker never takes over on its own."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Run the reopened stages now through the normal claim path instead of "
            "leaving them for the Worker."
        ),
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_MODEL_CONFIG,
        help="Provider/model YAML used only when a model-backed stage runs.",
    )
    parser.add_argument(
        "--sources-config",
        default=DEFAULT_SOURCES_CONFIG,
        help=(
            "Source YAML providing Source Mappers; used only when a facts "
            "stage actually maps a structured response."
        ),
    )


def _add_execute_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="Document UUID to process; repeat to process multiple Documents.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum latest Documents when no IDs are supplied (1-200).",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Run the stage again even when it already completed.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_MODEL_CONFIG,
        help="Provider/model YAML used only when a model-backed stage runs.",
    )
    parser.add_argument(
        "--sources-config",
        default=DEFAULT_SOURCES_CONFIG,
        help=(
            "Source YAML providing Source Mappers; used only when a facts "
            "stage actually maps a structured response."
        ),
    )


def _uuid_values(values: list[str] | None) -> list[UUID] | None:
    if not values:
        return None
    return [UUID(value) for value in values]


def _error(code: str, message: str, **extra) -> dict:
    payload = {"code": code, "message": message}
    payload.update(extra)
    return payload


def _fail(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        document_ids = _uuid_values(getattr(args, "document_ids", None))
    except ValueError:
        parser.error("--document-id must be a valid UUID")
        return 2

    if args.command == "status":
        return _status(args, document_ids)
    if args.command in {"retry", "reprocess"}:
        return _reopen(args, document_ids or [])
    if args.command == "facts" and (args.source_id or args.report_document_ids):
        return _report_facts(args)
    return _execute(args, document_ids, parser)


def _status(args, document_ids: list[UUID] | None) -> int:
    if not 1 <= args.limit <= 200:
        return _fail(_error("LIMIT_INVALID", "limit must be between 1 and 200"))
    try:
        registry = build_stage_registry(
            DEFAULT_MODEL_CONFIG,
            sources_config_path=DEFAULT_SOURCES_CONFIG,
        )
        with session_factory()() as session:
            rows = describe_stages(
                session,
                registry,
                document_ids=document_ids,
                stages=args.stage,
                statuses=args.status,
                limit=args.limit,
            )
    except (ValueError, OSError) as exc:
        return _fail(
            _error(
                "PROCESSING_UNAVAILABLE",
                "Document processing state could not be read",
                detail=str(exc),
            )
        )
    available = registry.available_stages()
    payload = {
        "items": rows,
        "counts": _counts(rows),
        "available_stages": sorted(available),
        "unavailable_stages": [
            stage for stage in PROCESSING_STAGES if stage not in available
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _counts(rows: list[dict]) -> dict:
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in PROCESSING_STAGE_STATUSES
    }
    counts["waiting_for_processor"] = sum(
        row["status"] == "pending" and row.get("processor_available") is False
        for row in rows
    )
    return counts


def _reopen(args, document_ids: list[UUID]) -> int:
    executions, errors = run_stages_for_documents(
        session_factory(),
        settings(),
        build_stage_registry(
            args.config, sources_config_path=args.sources_config
        ),
        document_ids,
        args.stage,
        reprocess=args.command == "reprocess",
        owner_token=f"cli-{uuid4().hex}",
        execute=args.execute,
        confirm_stopped=args.confirm_stopped,
    )
    payload: dict = {"items": [item.to_dict() for item in executions]}
    if errors:
        payload["errors"] = errors
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if errors else 0


STAGE_BY_COMMAND = {
    "parse": STAGE_PARSE,
    "index": STAGE_INDEX,
    "facts": STAGE_FACTS,
}


def _execute(args, document_ids: list[UUID] | None, parser) -> int:
    stage = STAGE_BY_COMMAND[args.command]
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")
    try:
        with session_factory()() as session:
            if document_ids:
                targets = list(document_ids)
            else:
                # Only actionable work by default: an ordinary manual run should
                # not report every already completed Document as processed again.
                targets = [
                    state.document_id
                    for state in list_stage_states(
                        session,
                        stages=[stage],
                        statuses=["pending", "failed"],
                        limit=args.limit,
                        # Only actionable work: an index stage of an unparsed
                        # Document is not runnable, and a bounded page must not be
                        # filled by rows this command can never claim.
                        dependencies_completed=True,
                    )
                ]
    except Exception:  # noqa: BLE001 - CLI must return only safe diagnostics
        return _fail(
            _error(
                "PROCESSING_UNAVAILABLE",
                "Document processing could not be initialized",
            )
        )

    executions, errors = run_stages_for_documents(
        session_factory(),
        settings(),
        build_stage_registry(
            args.config, sources_config_path=args.sources_config
        ),
        targets,
        [stage],
        reprocess=args.reprocess,
        owner_token=f"cli-{uuid4().hex}",
        completed_is_error=False,
    )
    items = [item.to_dict() for item in executions]
    if args.command == "parse" and args.include_text:
        with session_factory()() as session:
            for item in items:
                _attach_chunks(session, item)
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


def _report_facts(args) -> int:
    """Print stored Facts for a source or explicit Documents.

    This is a read-only view of what the ``facts`` stage already produced; it does
    not run the stage, so a report never triggers a model call.
    """

    from stocks.facts import (
        FactFilters,
        fact_to_dict,
        search_facts,
    )

    try:
        document_ids = [
            UUID(value) for value in (args.report_document_ids or [])
        ]
    except ValueError:
        return _fail(
            _error("DOCUMENT_ID_INVALID", "document id must be a valid UUID")
        )
    filters = FactFilters(
        source_ids=(args.source_id,) if args.source_id else (),
        document_ids=tuple(document_ids),
    )
    try:
        with session_factory()() as session:
            rows = search_facts(session, filters, limit=args.limit)
            items = [fact_to_dict(row) for row in rows]
    except (ValueError, OSError) as exc:
        return _fail(
            _error(
                "FACTS_UNAVAILABLE",
                "Stored Facts could not be read",
                detail=str(exc),
            )
        )
    print(
        json.dumps(
            {
                "count": len(items),
                "truncated": len(items) == args.limit,
                "filters": {
                    "source_ids": list(filters.source_ids),
                    "document_ids": [
                        str(value) for value in filters.document_ids
                    ],
                },
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _attach_chunks(session, item: dict) -> None:
    document = session.get(Document, UUID(item["document_id"]))
    if document is None:
        item["document"] = None
        item["chunks"] = []
        return
    batch_id = item.get("input_batch_id")
    chunks = list_parse_chunks(
        session, document.id, batch_id=UUID(batch_id) if batch_id else None
    )
    item["document"] = {
        "title": document.title,
        "version": document.version,
        "completeness": document.completeness,
        "content_type": document.content_type,
    }
    item["chunks"] = [
        {
            "id": str(chunk.id),
            "position": chunk.position,
            "text": chunk.text,
            "locator": chunk.locator,
        }
        for chunk in chunks
    ]


if __name__ == "__main__":
    raise SystemExit(main())
