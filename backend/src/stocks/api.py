from fastapi import FastAPI, Query
from sqlalchemy import select
from stocks.db import session_factory
from stocks.models import Document

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
                "content_type": d.content_type,
                "content_size": d.content_size,
                "content_hash": d.content_hash,
                "snapshot_key": d.snapshot_key,
            }
            for d in rows
        ]
