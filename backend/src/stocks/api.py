from fastapi import FastAPI, Query
from sqlalchemy import select

from stocks.db import session_factory
from stocks.models import Document, DocumentCollectionState, DocumentParseState

app = FastAPI(title="Stocks", version="0.1.0")

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

@app.get("/api/documents")
def documents(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    with session_factory()() as session:
        rows = session.scalars(
            select(Document).order_by(Document.collected_at.desc()).limit(limit)
        ).all()
        states = {
            (state.source_id, state.dedupe_key): state
            for state in session.scalars(
                select(DocumentCollectionState).where(
                    DocumentCollectionState.latest_document_id.in_(
                        [document.id for document in rows]
                    )
                )
            ).all()
        }
        parse_states = {
            state.document_id: state
            for state in session.scalars(
                select(DocumentParseState).where(
                    DocumentParseState.document_id.in_(
                        [document.id for document in rows]
                    )
                )
            ).all()
        }
        return [
            {
                "id": str(d.id),
                "source_id": d.source_id,
                "version": d.version,
                "title": d.title,
                "original_url": d.original_url,
                "published_at": d.published_at,
                "source_updated_at": d.source_updated_at,
                "collected_at": d.collected_at,
                "trust_tier": d.trust_tier_snapshot,
                "completeness": d.completeness,
                "fetch_error": d.fetch_error,
                "collection_state": _collection_state(states, d),
                "parse_state": _parse_state(parse_states.get(d.id)),
                "content_type": d.content_type,
                "content_size": d.content_size,
                "content_hash": d.content_hash,
                "snapshot_key": d.snapshot_key,
            }
            for d in rows
        ]


def _collection_state(
    states: dict[tuple[str, str], DocumentCollectionState], document: Document
) -> dict[str, object] | None:
    state = states.get((document.source_id, document.dedupe_key))
    if state is None:
        return None
    return {
        "latest_document_id": str(state.latest_document_id),
        "completeness": state.completeness,
        "fetch_error": state.fetch_error,
        "last_attempted_at": state.last_attempted_at,
    }


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
