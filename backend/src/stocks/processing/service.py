from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from stocks.models import Document, DocumentParseBatch
from stocks.processing.parser import (
    PROCESSOR_VERSION,
    ParseFailure,
    parse_content,
)
from stocks.processing.stages import (
    StageClaim,
    StageOutcome,
    apply_stage_outcome,
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
    stage_claim: StageClaim | None = None,
) -> ParseSummary:
    fingerprint = parse_config_fingerprint(config)
    if not force_reprocess:
        existing = find_parse_batch(
            session,
            document.id,
            processor_version=PROCESSOR_VERSION,
            config_fingerprint=fingerprint,
        )
        # A failed or unsupported attempt is not a reusable result: retrying it
        # must really re-read the original and record a new attempt.
        if existing is not None and existing.status == "completed":
            if stage_claim is not None:
                persist_parse_reuse(
                    session,
                    document.id,
                    batch_id=existing.id,
                    stage_claim=stage_claim,
                    processor_version=existing.processor_version,
                    config_fingerprint=existing.config_fingerprint,
                    attempted_at=(clock or (lambda: datetime.now(UTC)))(),
                )
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
        # The repository derives the stage status from the persisted batch, so a
        # failed parse never completes the stage.
        stage_claim=stage_claim,
    )
    return _summarize(document, result.batch, reused=result.reused)


def persist_parse_reuse(
    session: Session,
    document_id: UUID,
    *,
    batch_id: UUID,
    stage_claim: StageClaim,
    processor_version: str,
    config_fingerprint: str,
    attempted_at: datetime,
) -> None:
    """Complete a claimed parse stage from an already stored successful batch."""

    state = apply_stage_outcome(
        session,
        StageOutcome(
            document_id=stage_claim.document_id,
            stage=stage_claim.stage,
            owner_token=stage_claim.owner_token,
            status="completed",
            processor_version=processor_version,
            config_fingerprint=config_fingerprint,
            input_batch_id=batch_id,
        ),
        now=attempted_at,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)


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
