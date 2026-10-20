import hashlib
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stocks.models import (
    Document,
    DocumentChunk,
    DocumentCollectionState,
    DocumentParseBatch,
    DocumentParseState,
    Source,
)
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
    completeness: str = "complete"
    fetch_error: dict | None = None


@dataclass(frozen=True)
class IngestResult:
    document: Document
    created: bool


@dataclass(frozen=True)
class ParseChunkInput:
    text: str
    locator: dict


@dataclass(frozen=True)
class ParseBatchResult:
    batch: DocumentParseBatch
    state: DocumentParseState
    reused: bool = False


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
    if item.completeness not in {"complete", "partial"}:
        raise ValueError("completeness must be 'complete' or 'partial'")
    if item.completeness == "partial" and item.fetch_error is None:
        raise ValueError("partial content must include fetch_error")
    if item.completeness == "complete" and item.fetch_error is not None:
        raise ValueError("complete content must not include fetch_error")

    content_hash = hashlib.sha256(item.payload).hexdigest()
    rows = _document_rows(session, item)
    for existing in rows:
        if existing.content_hash == content_hash:
            _upsert_collection_state(session, item, existing)
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
            session.refresh(existing)
            return IngestResult(document=existing, created=False)

    try:
        object_key, stored_hash = store(
            config, item.source_id, item.payload, content_type=item.content_type
        )
    except Exception:
        session.rollback()
        raise
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
        completeness=item.completeness,
        fetch_error=deepcopy(item.fetch_error),
        content_type=item.content_type,
        content_size=len(item.payload),
        content_hash=content_hash,
        snapshot_key=object_key,
        trust_tier_snapshot=source.trust_tier,
    )
    session.add(document)
    try:
        session.flush()
        _upsert_collection_state(session, item, document)
        session.commit()
    except IntegrityError:
        session.rollback()
        for existing in _document_rows(session, item):
            if existing.content_hash == content_hash:
                return IngestResult(document=existing, created=False)
        raise
    except Exception:
        session.rollback()
        raise
    session.refresh(document)
    return IngestResult(document=document, created=True)


def _upsert_collection_state(
    session: Session, item: DocumentInput, document: Document
) -> None:
    state = session.get(
        DocumentCollectionState, (item.source_id, item.dedupe_key)
    )
    if state is None:
        state = DocumentCollectionState(
            source_id=item.source_id,
            dedupe_key=item.dedupe_key,
            latest_document_id=document.id,
            completeness=item.completeness,
            fetch_error=deepcopy(item.fetch_error),
            published_at=item.published_at,
            source_updated_at=item.source_updated_at,
            last_attempted_at=item.collected_at,
        )
        session.add(state)
    else:
        state.latest_document_id = document.id
        state.completeness = item.completeness
        state.fetch_error = deepcopy(item.fetch_error)
        state.published_at = item.published_at
        state.source_updated_at = item.source_updated_at
        state.last_attempted_at = item.collected_at


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


def get_latest_document(
    session: Session, source_id: str, dedupe_key: str
) -> Document | None:
    return session.scalar(
        select(Document)
        .where(
            Document.source_id == source_id,
            Document.dedupe_key == dedupe_key,
        )
        .order_by(Document.version.desc())
        .limit(1)
    )


def get_collection_state(
    session: Session, source_id: str, dedupe_key: str
) -> DocumentCollectionState | None:
    return session.get(DocumentCollectionState, (source_id, dedupe_key))


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


def find_parse_batch(
    session: Session,
    document_id: UUID,
    *,
    processor_version: str,
    config_fingerprint: str,
) -> DocumentParseBatch | None:
    return session.scalar(
        select(DocumentParseBatch)
        .where(
            DocumentParseBatch.document_id == document_id,
            DocumentParseBatch.processor_version == processor_version,
            DocumentParseBatch.config_fingerprint == config_fingerprint,
        )
        .order_by(DocumentParseBatch.attempt_number.desc())
        .limit(1)
    )


def get_parse_state(session: Session, document_id: UUID) -> DocumentParseState | None:
    return session.get(DocumentParseState, document_id)


def get_parse_batch(session: Session, batch_id: UUID) -> DocumentParseBatch | None:
    return session.get(DocumentParseBatch, batch_id)


def list_parse_batches(
    session: Session, document_id: UUID
) -> list[DocumentParseBatch]:
    return list(
        session.scalars(
            select(DocumentParseBatch)
            .where(DocumentParseBatch.document_id == document_id)
            .order_by(DocumentParseBatch.attempt_number)
        ).all()
    )


def list_parse_chunks(
    session: Session,
    document_id: UUID,
    *,
    batch_id: UUID | None = None,
    current: bool = True,
) -> list[DocumentChunk]:
    selected_batch = batch_id
    if selected_batch is None:
        state = get_parse_state(session, document_id)
        if state is None:
            return []
        selected_batch = (
            state.current_batch_id if current else state.latest_batch_id
        )
    if selected_batch is None:
        return []
    return list(
        session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.parse_batch_id == selected_batch,
            )
            .order_by(DocumentChunk.position)
        ).all()
    )


def persist_parse_result(
    session: Session,
    document_id: UUID,
    *,
    processor_version: str,
    config_fingerprint: str,
    content_hash: str,
    status: str,
    error: dict | None,
    chunks: tuple[ParseChunkInput, ...],
    attempted_at: datetime,
) -> ParseBatchResult:
    if status not in {"completed", "failed", "unsupported"}:
        raise ValueError("invalid parse status")
    if status == "completed" and (not chunks or error is not None):
        raise ValueError("completed parse must contain chunks and no error")
    if status != "completed" and chunks:
        raise ValueError("failed or unsupported parse must not contain chunks")
    if status != "completed" and error is None:
        raise ValueError("failed or unsupported parse must include an error")
    if any(not chunk.text.strip() for chunk in chunks):
        raise ValueError("chunk text must not be empty")

    document = session.scalar(
        select(Document).where(Document.id == document_id).with_for_update()
    )
    if document is None:
        raise ValueError(f"Unknown document: {document_id}")
    if document.content_hash != content_hash:
        raise ValueError("Document content hash changed during processing")
    attempt_number = (
        session.scalar(
            select(func.max(DocumentParseBatch.attempt_number)).where(
                DocumentParseBatch.document_id == document_id,
            )
        )
        or 0
    ) + 1
    batch = DocumentParseBatch(
        document_id=document_id,
        processor_version=processor_version,
        config_fingerprint=config_fingerprint,
        content_hash=content_hash,
        attempt_number=attempt_number,
        status=status,
        error=deepcopy(error),
        chunk_count=len(chunks),
        created_at=attempted_at,
    )
    session.add(batch)
    try:
        session.flush()
        for position, chunk in enumerate(chunks):
            session.add(
                DocumentChunk(
                    document_id=document_id,
                    parse_batch_id=batch.id,
                    position=position,
                    text=chunk.text,
                    locator=deepcopy(chunk.locator),
                )
            )
        state = session.get(DocumentParseState, document_id)
        if state is None:
            state = DocumentParseState(
                document_id=document_id,
                status=status,
                processor_version=processor_version,
                config_fingerprint=config_fingerprint,
                latest_batch_id=batch.id,
                current_batch_id=batch.id if status == "completed" else None,
                error=deepcopy(error),
                chunk_count=len(chunks),
                attempted_at=attempted_at,
            )
            session.add(state)
        else:
            state.status = status
            state.processor_version = processor_version
            state.config_fingerprint = config_fingerprint
            state.latest_batch_id = batch.id
            if status == "completed":
                state.current_batch_id = batch.id
            state.error = deepcopy(error)
            state.chunk_count = len(chunks)
            state.attempted_at = attempted_at
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(batch)
    assert state is not None
    session.refresh(state)
    return ParseBatchResult(batch=batch, state=state)
