import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stocks.models import Document, Source
from stocks.s3 import read_snapshot, store_snapshot
from stocks.settings import Settings


@dataclass(frozen=True)
class DocumentInput:
    source_id: str
    dedupe_key: str
    title: str
    payload: bytes
    content_type: str
    collected_at: datetime
    original_url: str | None = None
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    summary: str | None = None


@dataclass(frozen=True)
class IngestResult:
    document: Document
    created: bool


def ingest_document(
    session: Session,
    config: Settings,
    item: DocumentInput,
    *,
    store: Callable[..., tuple[str, str]] = store_snapshot,
) -> IngestResult:
    source = session.get(Source, item.source_id)
    if source is None:
        raise ValueError(f"Unknown source: {item.source_id}")
    if not item.content_type.strip():
        raise ValueError("content_type must not be empty")
    if not item.title.strip():
        raise ValueError("title must not be empty")
    if not item.dedupe_key.strip():
        raise ValueError("dedupe_key must not be empty")

    content_hash = hashlib.sha256(item.payload).hexdigest()
    rows = _document_rows(session, item)
    for existing in rows:
        if existing.content_hash == content_hash:
            return IngestResult(document=existing, created=False)

    object_key, stored_hash = store(
        config, item.source_id, item.payload, content_type=item.content_type
    )
    if stored_hash != content_hash:
        raise RuntimeError("Stored content hash does not match the supplied bytes")

    next_version = max((row.version for row in rows), default=0) + 1
    document = Document(
        source_id=item.source_id,
        dedupe_key=item.dedupe_key,
        version=next_version,
        title=item.title,
        original_url=item.original_url,
        published_at=item.published_at,
        source_updated_at=item.source_updated_at,
        collected_at=item.collected_at,
        summary=item.summary,
        content_type=item.content_type,
        content_size=len(item.payload),
        content_hash=content_hash,
        snapshot_key=object_key,
        trust_tier_snapshot=source.trust_tier,
    )
    session.add(document)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        for existing in _document_rows(session, item):
            if existing.content_hash == content_hash:
                return IngestResult(document=existing, created=False)
        raise
    session.refresh(document)
    return IngestResult(document=document, created=True)


def _document_rows(session: Session, item: DocumentInput) -> list[Document]:
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.source_id == item.source_id,
                Document.dedupe_key == item.dedupe_key,
            )
            .order_by(Document.version.desc())
        ).all()
    )


def get_document(session: Session, document_id: UUID) -> Document | None:
    return session.get(Document, document_id)


def list_document_versions(session: Session, source_id: str, dedupe_key: str) -> list[Document]:
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.source_id == source_id,
                Document.dedupe_key == dedupe_key,
            )
            .order_by(Document.version)
        ).all()
    )


def read_document_bytes(config: Settings, document: Document) -> bytes:
    return read_snapshot(config, document.snapshot_key)
