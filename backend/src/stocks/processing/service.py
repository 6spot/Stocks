from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.models import Document, DocumentParseBatch
from stocks.processing.parser import (
    PROCESSOR_VERSION,
    ParseFailure,
    parse_content,
)
from stocks.repository import (
    ParseChunkInput,
    find_parse_batch,
    persist_parse_result,
    read_document_bytes,
)
from stocks.settings import Settings


@dataclass(frozen=True)
class ParseSummary:
    document_id: UUID
    version: int
    status: str
    batch_id: UUID
    chunk_count: int
    processor_version: str
    config_fingerprint: str
    reused: bool
    error: dict | None = None

    def to_dict(self) -> dict:
        result = {
            "document_id": str(self.document_id),
            "version": self.version,
            "status": self.status,
            "batch_id": str(self.batch_id),
            "chunk_count": self.chunk_count,
            "processor_version": self.processor_version,
            "config_fingerprint": self.config_fingerprint,
            "reused": self.reused,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


def parse_config_fingerprint(config: Settings) -> str:
    value = (
        f"{PROCESSOR_VERSION}:"
        f"size={config.parse_chunk_size}:"
        f"overlap={config.parse_chunk_overlap}"
    )
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def process_document(
    session: Session,
    config: Settings,
    document: Document,
    *,
    force_reprocess: bool = False,
    read_bytes: Callable[[Settings, Document], bytes] = read_document_bytes,
    clock: Callable[[], datetime] | None = None,
) -> ParseSummary:
    fingerprint = parse_config_fingerprint(config)
    if not force_reprocess:
        existing = find_parse_batch(
            session,
            document.id,
            processor_version=PROCESSOR_VERSION,
            config_fingerprint=fingerprint,
        )
        if existing is not None:
            return _summarize(document, existing, reused=True)

    attempted_at = (clock or (lambda: datetime.now(UTC)))()
    try:
        payload = read_bytes(config, document)
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != document.content_hash:
            raise ParseFailure(
                "CONTENT_HASH_MISMATCH",
                "Stored document bytes do not match the registered content hash",
            )
        _, chunks = parse_content(
            payload,
            document.content_type,
            chunk_size=config.parse_chunk_size,
            chunk_overlap=config.parse_chunk_overlap,
        )
        chunk_inputs = tuple(
            ParseChunkInput(text=chunk.text, locator=chunk.locator)
            for chunk in chunks
        )
    except ParseFailure as exc:
        status = exc.status
        error: dict | None = exc.to_dict()
        chunk_inputs = ()
    else:
        status = "completed"
        error = None
    result = persist_parse_result(
        session,
        document.id,
        processor_version=PROCESSOR_VERSION,
        config_fingerprint=fingerprint,
        content_hash=document.content_hash,
        status=status,
        error=error,
        chunks=chunk_inputs,
        attempted_at=attempted_at,
    )
    return _summarize(document, result.batch, reused=result.reused)


def process_documents(
    session: Session,
    config: Settings,
    *,
    document_ids: list[UUID] | None = None,
    limit: int = 20,
    force_reprocess: bool = False,
    read_bytes: Callable[[Settings, Document], bytes] = read_document_bytes,
    clock: Callable[[], datetime] | None = None,
) -> list[ParseSummary]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if document_ids:
        documents = list(
            session.scalars(
                select(Document).where(Document.id.in_(document_ids))
            ).all()
        )
        by_id = {document.id: document for document in documents}
        missing = [document_id for document_id in document_ids if document_id not in by_id]
        if missing:
            raise ValueError(
                "Unknown document IDs: "
                + ", ".join(str(document_id) for document_id in missing)
            )
        ordered = [
            by_id[document_id]
            for document_id in document_ids
        ]
    else:
        ordered = list(
            session.scalars(
                select(Document)
                .order_by(Document.collected_at.desc())
                .limit(limit)
            ).all()
        )
    return [
        process_document(
            session,
            config,
            document,
            force_reprocess=force_reprocess,
            read_bytes=read_bytes,
            clock=clock,
        )
        for document in ordered
    ]


def _summarize(
    document: Document,
    batch: DocumentParseBatch,
    *,
    reused: bool,
) -> ParseSummary:
    return ParseSummary(
        document_id=document.id,
        version=document.version,
        status=batch.status,
        batch_id=batch.id,
        chunk_count=batch.chunk_count,
        processor_version=batch.processor_version,
        config_fingerprint=batch.config_fingerprint,
        reused=reused,
        error=batch.error,
    )
