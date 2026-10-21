from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stocks.db import session_factory
from stocks.facts import FactFilters, fact_to_dict, list_document_facts, search_facts
from stocks.models import (
    Analysis,
    Document,
    DocumentChunk,
    DocumentCollectionState,
    DocumentEmbeddingState,
    DocumentParseState,
    EvidencePackage,
    Report,
    TaskRun,
)
from stocks.processing.parser import HTML_CONTENT_TYPES, TEXT_CONTENT_TYPES
from stocks.repository import (
    get_document,
    list_document_versions,
    list_parse_chunks,
    read_document_bytes,
)
from stocks.settings import Settings, settings

logger = logging.getLogger(__name__)

app = FastAPI(title="Stocks", version="0.1.0")

SNAPSHOT_UNAVAILABLE = "SNAPSHOT_STORAGE_UNAVAILABLE"
SAFE_TEXT_CONTENT_TYPES = HTML_CONTENT_TYPES | TEXT_CONTENT_TYPES


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/documents")
def documents(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    with session_factory()() as session:
        rows = session.scalars(
            select(Document).order_by(Document.collected_at.desc()).limit(limit)
        ).all()
        states, parse_states, embedding_states, current_chunk_counts = _states(
            session, [document.id for document in rows]
        )
        return [
            {
                **_document_payload(d),
                "collection_state": _collection_state(states, d),
                "parse_state": _parse_state(parse_states.get(d.id)),
                "embedding_state": _embedding_state(
                    embedding_states.get(d.id),
                    parse_states.get(d.id),
                    current_chunk_counts.get(d.id, 0),
                ),
            }
            for d in rows
        ]


@app.get("/api/facts")
def facts(
    source_id: list[str] | None = Query(None),
    document_id: list[UUID] | None = Query(None),
    fact_type: list[str] | None = Query(None),
    symbol: list[str] | None = Query(None),
    metric_id: list[str] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Read stored Facts by source, document, type, symbol or metric.

    This is a bounded structured query over already-extracted data. It never runs
    extraction, and a Fact always keeps the Document version and field or Chunk it
    came from, so a reader can resolve it back to the original bytes.
    """

    filters = FactFilters(
        source_ids=tuple(source_id or ()),
        document_ids=tuple(document_id or ()),
        fact_types=tuple(fact_type or ()),
        symbols=tuple(symbol or ()),
        metric_ids=tuple(metric_id or ()),
    )
    with session_factory()() as session:
        rows = search_facts(session, filters, limit=limit)
    return {
        "count": len(rows),
        # A full page means more rows may exist; an empty or short page does not
        # promise anything about unexamined candidates.
        "truncated": len(rows) == limit,
        "items": [fact_to_dict(row) for row in rows],
    }


@app.get("/api/documents/{document_id}/facts")
def document_facts(document_id: UUID) -> dict:
    """Every stored Fact of one Document version, in creation order."""

    with session_factory()() as session:
        if get_document(session, document_id) is None:
            raise HTTPException(status_code=404, detail="Unknown document")
        rows = list_document_facts(session, document_id)
    return {
        "count": len(rows),
        "items": [fact_to_dict(row) for row in rows],
    }


@app.get("/api/documents/{document_id}")
def document_detail(document_id: UUID) -> dict:
    config = settings()
    with session_factory()() as session:
        document = get_document(session, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Unknown document")
        states, parse_states, embedding_states, current_chunk_counts = _states(
            session, [document.id]
        )
        versions = list_document_versions(
            session, document.source_id, document.dedupe_key
        )
        pointer = states.get((document.source_id, document.dedupe_key))
        return {
            "document": _document_payload(document),
            "collection_state": _collection_state(states, document),
            "parse_state": _parse_state(parse_states.get(document.id)),
            "embedding_state": _embedding_state(
                embedding_states.get(document.id),
                parse_states.get(document.id),
                current_chunk_counts.get(document.id, 0),
            ),
            "is_current_collection_pointer": bool(
                pointer is not None and pointer.latest_document_id == document.id
            ),
            "versions": [
                {
                    "document_id": str(version.id),
                    "version": version.version,
                    "content_hash": version.content_hash,
                    "content_size": version.content_size,
                    "content_type": version.content_type,
                    "completeness": version.completeness,
                    "collected_at": version.collected_at,
                    "is_current_collection_pointer": bool(
                        pointer is not None and pointer.latest_document_id == version.id
                    ),
                }
                for version in versions
            ],
            "current_chunks": [
                {
                    "chunk_id": str(chunk.id),
                    "position": chunk.position,
                    "text": chunk.text,
                    "locator": chunk.locator,
                }
                for chunk in list_parse_chunks(session, document.id)
            ],
            "original": _original_descriptor(config, document),
        }


@app.get("/api/documents/{document_id}/original")
def document_original(document_id: UUID, download: bool = Query(False)):
    config = settings()
    with session_factory()() as session:
        document = get_document(session, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Unknown document")
        payload = _read_original_bytes(config, document)
        recorded_hash = document.content_hash
        content_type = document.content_type
        version = document.version

    digest = hashlib.sha256(payload).hexdigest()
    headers = {
        # The body is the stored bytes unchanged, so the recorded hash must match.
        "X-Content-Sha256": digest,
        "X-Content-Hash-Match": "true" if digest == recorded_hash else "false",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
    }
    if digest != recorded_hash:
        logger.warning(
            "Snapshot bytes do not match the stored hash for document %s", document_id
        )
    media_type = _serve_media_type(content_type)
    if download:
        suffix = "txt" if media_type == "text/plain" else "bin"
        headers["Content-Disposition"] = (
            f'attachment; filename="document-{document_id}-v{version}.{suffix}"'
        )
    return Response(content=payload, media_type=media_type, headers=headers)


@app.get("/api/reports")
def reports(
    task_id: str | None = Query(None, max_length=120),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    with session_factory()() as session:
        statement = select(Report).order_by(Report.created_at.desc()).limit(limit)
        if task_id is not None:
            statement = statement.where(Report.task_id == task_id)
        rows = session.scalars(statement).all()
        runs = _runs(session, [report.run_id for report in rows])
        return [
            {
                "report_id": str(report.id),
                "run_id": str(report.run_id),
                "analysis_id": str(report.analysis_id),
                "task_id": report.task_id,
                "title": report.title,
                "created_at": report.created_at,
                "run_status": (
                    runs[report.run_id].status if report.run_id in runs else None
                ),
            }
            for report in rows
        ]


@app.get("/api/reports/{report_id}")
def report_detail(report_id: UUID) -> dict:
    config = settings()
    with session_factory()() as session:
        report, analysis, package = _report_chain(session, report_id)
        run = session.get(TaskRun, report.run_id)
        entries, states = _resolve_evidence(
            session, config, _selected_chunks(package)
        )
        cited = _cited_keys(analysis)
        return {
            "report": {
                "report_id": str(report.id),
                "run_id": str(report.run_id),
                "analysis_id": str(report.analysis_id),
                "task_id": report.task_id,
                "title": report.title,
                "content": report.content,
                "created_at": report.created_at,
            },
            "run": _run_payload(run),
            "analysis": {
                "summary": analysis.summary,
                "findings": analysis.findings,
                "open_questions": analysis.open_questions,
                "evidence_refs": analysis.evidence_refs,
                "created_at": analysis.created_at,
            },
            "evidence_package": _package_payload(package),
            "evidence": entries,
            "cited_chunk_ids": [
                entry["chunk_id"]
                for entry in entries
                if (entry["chunk_id"], entry["document_id"]) in cited
            ],
            "coverage_gaps": _gaps(package, entries, run),
            "latest_collection_states": [
                _collection_state_payload(state)
                for state in sorted(
                    states.values(),
                    key=lambda item: (item.source_id, item.dedupe_key),
                )
            ],
        }


@app.get("/api/reports/{report_id}/evidence/{chunk_id}")
def report_evidence(report_id: UUID, chunk_id: UUID) -> dict:
    config = settings()
    with session_factory()() as session:
        report, _analysis, package = _report_chain(session, report_id)
        matching = [
            item
            for item in _selected_chunks(package)
            if _parse_id(item.get("chunk_id")) == chunk_id
        ]
        if not matching:
            raise HTTPException(
                status_code=404,
                detail="Chunk is not part of this report's Evidence Package",
            )
        entries, states = _resolve_evidence(session, config, matching)
        entry = entries[0]
        document = entry["document"]
        versions: list[dict] = []
        if document["source_id"] and document["dedupe_key"]:
            versions = [
                {
                    "document_id": str(version.id),
                    "version": version.version,
                    "content_hash": version.content_hash,
                    "completeness": version.completeness,
                    "collected_at": version.collected_at,
                }
                for version in list_document_versions(
                    session, document["source_id"], document["dedupe_key"]
                )
            ]
        return {
            "report_id": str(report.id),
            "run_id": str(report.run_id),
            "evidence_package_id": str(package.id),
            "as_of": package.as_of,
            "evidence": entry,
            "document_versions": versions,
            "latest_collection_state": _collection_state_payload(
                states.get((document["source_id"], document["dedupe_key"]))
            ),
        }


def _report_chain(
    session: Session, report_id: UUID
) -> tuple[Report, Analysis, EvidencePackage]:
    """Read a report with the Analysis and Evidence Package it was built from."""
    report = session.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Unknown report")
    analysis = session.get(Analysis, report.analysis_id)
    if analysis is None:
        raise HTTPException(
            status_code=409, detail="Report is missing its saved Analysis"
        )
    package = session.get(EvidencePackage, analysis.evidence_package_id)
    if package is None:
        raise HTTPException(
            status_code=409, detail="Analysis is missing its Evidence Package"
        )
    return report, analysis, package


def _document_payload(document: Document) -> dict:
    return {
        "id": str(document.id),
        "source_id": document.source_id,
        "dedupe_key": document.dedupe_key,
        "version": document.version,
        "title": document.title,
        "original_url": document.original_url,
        "published_at": document.published_at,
        "source_updated_at": document.source_updated_at,
        "collected_at": document.collected_at,
        "trust_tier": document.trust_tier_snapshot,
        "completeness": document.completeness,
        "fetch_error": document.fetch_error,
        "content_type": document.content_type,
        "content_size": document.content_size,
        "content_hash": document.content_hash,
        "snapshot_key": document.snapshot_key,
        # The current lightweight classification of this content version, or
        # ``None`` when no classification result exists yet. It is an attribute of
        # the stored version, never a replacement for the original text.
        "classification": document.classification,
    }


def _stored_document_payload(document: Document | None, item: dict) -> dict:
    if document is not None:
        return _document_payload(document)
    # A saved Evidence Package keeps the identity and acquisition times it used.
    # They stay readable when the row itself is missing, marked as not stored.
    return {
        "id": item.get("document_id"),
        "source_id": item.get("source_id"),
        "dedupe_key": None,
        "version": item.get("document_version"),
        "title": item.get("title"),
        "original_url": None,
        "published_at": item.get("published_at"),
        "source_updated_at": item.get("source_updated_at"),
        "collected_at": item.get("collected_at"),
        "trust_tier": None,
        "completeness": item.get("completeness"),
        "fetch_error": None,
        "content_type": None,
        "content_size": None,
        "content_hash": None,
        "snapshot_key": None,
    }


def _original_descriptor(config: Settings, document: Document | None) -> dict:
    if document is None:
        return {
            "stored": False,
            "readable": False,
            "storage_key": None,
            "content_type": None,
            "content_size": None,
            "content_hash": None,
            "endpoint": None,
        }
    return {
        "stored": True,
        "readable": _snapshot_configured(config),
        "storage_key": document.snapshot_key,
        "content_type": document.content_type,
        "content_size": document.content_size,
        "content_hash": document.content_hash,
        "endpoint": f"/api/documents/{document.id}/original",
    }


def _snapshot_configured(config: Settings) -> bool:
    return bool(
        config.s3_endpoint_url
        and config.s3_bucket.strip()
        and config.s3_access_key
        and config.s3_secret_key
    )


def _read_original_bytes(config: Settings, document: Document) -> bytes:
    if not _snapshot_configured(config):
        raise HTTPException(
            status_code=503,
            detail={
                "code": SNAPSHOT_UNAVAILABLE,
                "message": "Snapshot storage is not configured on this server",
            },
        )
    try:
        return read_document_bytes(config, document)
    except Exception as exc:  # noqa: BLE001 - storage failure is a stable 503
        logger.warning(
            "Snapshot read failed for document %s: %s", document.id, type(exc).__name__
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": SNAPSHOT_UNAVAILABLE,
                "message": (
                    "The stored original bytes could not be read from snapshot storage"
                ),
            },
        ) from exc


def _serve_media_type(content_type: str) -> str:
    """Never hand a stored HTML page back as HTML.

    The exact stored bytes are served, so the response hash keeps matching the
    Document's recorded evidence; only the media type is downgraded to safe text
    or an opaque stream, and `nosniff` plus the empty CSP stop a browser from
    executing anything the source published.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in SAFE_TEXT_CONTENT_TYPES:
        return "text/plain"
    return "application/octet-stream"


def _runs(session: Session, run_ids: list[UUID]) -> dict[UUID, TaskRun]:
    if not run_ids:
        return {}
    return {
        run.id: run
        for run in session.scalars(select(TaskRun).where(TaskRun.id.in_(run_ids))).all()
    }


def _run_payload(run: TaskRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "run_id": str(run.id),
        "task_id": run.task_id,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "retry_of_run_id": (
            str(run.retry_of_run_id) if run.retry_of_run_id else None
        ),
        "objective": run.objective,
        "strategy": run.strategy,
        "scope": run.scope,
        "config_hash": run.config_hash,
        "model_snapshot": run.model_snapshot,
        "output": run.output,
        "error": run.error,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _selected_chunks(package: EvidencePackage) -> list[dict]:
    selected = package.selected_chunks
    if not isinstance(selected, list):
        raise HTTPException(
            status_code=409, detail="Evidence Package selected chunks are invalid"
        )
    return [item for item in selected if isinstance(item, dict)]


def _package_payload(package: EvidencePackage) -> dict:
    return {
        "evidence_package_id": str(package.id),
        "run_id": str(package.run_id),
        "objective": package.objective,
        "scope": package.scope,
        "as_of": package.as_of,
        "selection_policy_version": package.selection_policy_version,
        "selection_budget": package.selection_budget,
        "queries": package.queries,
        "coverage": package.coverage,
        "candidates_discovered": package.candidates_discovered,
        "candidates_screened": package.candidates_screened,
        "selected_count": package.selected_count,
        "unexamined_count": package.unexamined_count,
        "retrieval_truncated": package.retrieval_truncated,
        "created_at": package.created_at,
    }


def _cited_keys(analysis: Analysis) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for ref in analysis.evidence_refs or []:
        if isinstance(ref, dict):
            keys.add((str(ref.get("chunk_id")), str(ref.get("document_id"))))
    for finding in analysis.findings or []:
        if not isinstance(finding, dict):
            continue
        for ref in finding.get("evidence_refs") or []:
            if isinstance(ref, dict):
                keys.add((str(ref.get("chunk_id")), str(ref.get("document_id"))))
    return keys


def _gaps(
    package: EvidencePackage,
    entries: list[dict],
    run: TaskRun | None,
) -> list[str]:
    """Combine recorded selection gaps with what reading the evidence shows now."""
    recorded = package.coverage.get("gaps", []) if package.coverage else []
    if not isinstance(recorded, list):
        raise HTTPException(
            status_code=409, detail="Evidence Package coverage gaps are invalid"
        )
    observed: list[str] = []
    for entry in entries:
        if not entry["original"]["stored"]:
            observed.append("document_not_stored")
        if not entry["chunk_available"]:
            observed.append("chunk_unavailable")
        if entry["document"]["completeness"] == "partial":
            observed.append("partial_source_content")
        if entry["original"]["stored"] and not entry["original"]["readable"]:
            observed.append("snapshot_storage_unconfigured")
        if not entry["parse_is_current"]:
            observed.append("chunk_not_in_current_parse_batch")
        if entry["embedding_state"] is None or entry["embedding_state"].get(
            "current"
        ) is not True:
            observed.append("chunk_not_in_current_index")
    if run is not None and run.status != "succeeded":
        observed.append("run_not_succeeded")
    return list(dict.fromkeys([*(str(gap) for gap in recorded), *observed]))


def _ids(values: list[object]) -> list[UUID]:
    return list(dict.fromkeys(parsed for parsed in map(_parse_id, values) if parsed))


def _parse_id(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _states(
    session: Session, document_ids: list[UUID]
) -> tuple[
    dict[tuple[str, str], DocumentCollectionState],
    dict[UUID, DocumentParseState],
    dict[UUID, DocumentEmbeddingState],
    dict[UUID, int],
]:
    unique_ids = list(dict.fromkeys(document_ids))
    if not unique_ids:
        return {}, {}, {}, {}
    documents = list(
        session.scalars(select(Document).where(Document.id.in_(unique_ids))).all()
    )
    parse_states = {
        state.document_id: state
        for state in session.scalars(
            select(DocumentParseState).where(
                DocumentParseState.document_id.in_(unique_ids)
            )
        ).all()
    }
    embedding_states = {
        state.document_id: state
        for state in session.scalars(
            select(DocumentEmbeddingState).where(
                DocumentEmbeddingState.document_id.in_(unique_ids)
            )
        ).all()
    }
    current_chunk_counts = dict(
        session.execute(
            select(
                DocumentChunk.document_id,
                func.count(DocumentChunk.id),
            )
            .join(
                DocumentParseState,
                DocumentParseState.document_id == DocumentChunk.document_id,
            )
            .where(
                DocumentParseState.current_batch_id == DocumentChunk.parse_batch_id,
                DocumentChunk.document_id.in_(unique_ids),
            )
            .group_by(DocumentChunk.document_id)
        ).all()
    )
    keys = {(document.source_id, document.dedupe_key) for document in documents}
    collection_states: dict[tuple[str, str], DocumentCollectionState] = {}
    if keys:
        source_ids = {source_id for source_id, _ in keys}
        dedupe_keys = {dedupe_key for _, dedupe_key in keys}
        for state in session.scalars(
            select(DocumentCollectionState).where(
                DocumentCollectionState.source_id.in_(source_ids),
                DocumentCollectionState.dedupe_key.in_(dedupe_keys),
            )
        ).all():
            if (state.source_id, state.dedupe_key) in keys:
                collection_states[(state.source_id, state.dedupe_key)] = state
    return collection_states, parse_states, embedding_states, current_chunk_counts


def _resolve_evidence(
    session: Session,
    config: Settings,
    selected: list[dict],
) -> tuple[list[dict], dict[tuple[str, str], DocumentCollectionState]]:
    """Read each selected Chunk at the Document version the report actually used.

    A report is always shown with its own evidence. The current collection
    pointer is returned separately, so a newer Document version can be visible
    without ever rewriting what an older report cited.
    """
    chunk_ids = _ids([item.get("chunk_id") for item in selected])
    document_ids = _ids([item.get("document_id") for item in selected])
    chunks = (
        {
            chunk.id: chunk
            for chunk in session.scalars(
                select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
            ).all()
        }
        if chunk_ids
        else {}
    )
    documents = (
        {
            document.id: document
            for document in session.scalars(
                select(Document).where(Document.id.in_(document_ids))
            ).all()
        }
        if document_ids
        else {}
    )
    states, parse_states, embedding_states, current_chunk_counts = _states(
        session, list(documents)
    )

    entries: list[dict] = []
    for item in selected:
        chunk_id = _parse_id(item.get("chunk_id"))
        document_id = _parse_id(item.get("document_id"))
        batch_id = _parse_id(item.get("parse_batch_id"))
        document = documents.get(document_id) if document_id else None
        chunk = chunks.get(chunk_id) if chunk_id else None
        chunk_matches = (
            chunk is not None
            and document is not None
            and chunk.document_id == document.id
            and (batch_id is None or chunk.parse_batch_id == batch_id)
            and (
                item.get("position") is None
                or chunk.position == int(item["position"])
            )
        )
        parse_state = parse_states.get(document.id) if document else None
        embedding_state = _embedding_state(
            embedding_states.get(document.id) if document else None,
            parse_state,
            current_chunk_counts.get(document.id, 0) if document else 0,
        )
        payload = _stored_document_payload(document, item)
        key = (payload["source_id"], payload["dedupe_key"])
        state = states.get(key) if all(key) else None
        entries.append(
            {
                "chunk_id": str(chunk_id) if chunk_id else item.get("chunk_id"),
                "document_id": (
                    str(document_id) if document_id else item.get("document_id")
                ),
                "parse_batch_id": (
                    str(batch_id) if batch_id else item.get("parse_batch_id")
                ),
                "position": chunk.position if chunk_matches else item.get("position"),
                "document_version": payload["version"],
                "cited_document_version": item.get("document_version"),
                "version_matches": (
                    document is not None
                    and item.get("document_version") is not None
                    and document.version == int(item["document_version"])
                ),
                "role": item.get("role"),
                "reason": item.get("reason"),
                "similarity": item.get("similarity"),
                "query_rank": item.get("query_rank"),
                "estimated_tokens": item.get("estimated_tokens"),
                "chunk_available": bool(chunk_matches),
                "text": chunk.text if chunk_matches else None,
                "locator": chunk.locator if chunk_matches else None,
                "parse_is_current": bool(
                    parse_state is not None
                    and chunk is not None
                    and parse_state.current_batch_id == chunk.parse_batch_id
                ),
                "parse_state": _parse_state(parse_state),
                "embedding_state": embedding_state,
                "document": payload,
                "original": _original_descriptor(config, document),
                "is_current_collection_pointer": bool(
                    state is not None and str(state.latest_document_id) == payload["id"]
                ),
                "collection_state": _collection_state_payload(state),
            }
        )
    return entries, states


def _collection_state_payload(
    state: DocumentCollectionState | None,
) -> dict | None:
    if state is None:
        return None
    return {
        "source_id": state.source_id,
        "dedupe_key": state.dedupe_key,
        "latest_document_id": str(state.latest_document_id),
        "completeness": state.completeness,
        "fetch_error": state.fetch_error,
        "published_at": state.published_at,
        "source_updated_at": state.source_updated_at,
        "last_attempted_at": state.last_attempted_at,
    }


def _collection_state(
    states: dict[tuple[str, str], DocumentCollectionState], document: Document
) -> dict[str, object] | None:
    return _collection_state_payload(
        states.get((document.source_id, document.dedupe_key))
    )


def _parse_state(state: DocumentParseState | None) -> dict[str, object] | None:
    if state is None:
        return None
    return {
        "status": state.status,
        "processor_version": state.processor_version,
        "latest_batch_id": (
            str(state.latest_batch_id) if state.latest_batch_id else None
        ),
        "current_batch_id": (
            str(state.current_batch_id) if state.current_batch_id else None
        ),
        "chunk_count": state.chunk_count,
        "error": state.error,
        "attempted_at": state.attempted_at,
    }


def _embedding_state(
    state: DocumentEmbeddingState | None,
    parse_state: DocumentParseState | None,
    current_chunk_count: int,
) -> dict[str, object] | None:
    if (
        parse_state is None
        or parse_state.current_batch_id is None
        or current_chunk_count == 0
    ):
        return None
    is_current_batch = (
        state is not None
        and state.parse_batch_id == parse_state.current_batch_id
    )
    indexed_count = state.indexed_count if is_current_batch else 0
    return {
        "status": state.status if is_current_batch else "pending",
        "current": is_current_batch,
        "parse_batch_id": str(parse_state.current_batch_id),
        "last_attempt_parse_batch_id": (
            str(state.parse_batch_id) if state is not None else None
        ),
        "model_alias": state.model_alias if state is not None else None,
        "model_name": state.model_name if state is not None else None,
        "dimensions": state.dimensions if state is not None else None,
        "chunk_count": current_chunk_count,
        "indexed_count": indexed_count,
        "unindexed_count": current_chunk_count - indexed_count,
        "error": state.error if is_current_batch else None,
        "attempted_at": state.attempted_at if state is not None else None,
    }
