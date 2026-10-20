from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    connector: Mapped[str] = mapped_column(String(40))
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trust_tier: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "completeness IN ('complete', 'partial')",
            name="ck_documents_completeness",
        ),
        UniqueConstraint(
            "source_id",
            "dedupe_key",
            "content_hash",
            name="uq_documents_source_content",
        ),
        UniqueConstraint(
            "source_id",
            "dedupe_key",
            "version",
            name="uq_documents_source_version",
        ),
        Index("ix_documents_collected", "collected_at"),
        Index("ix_documents_source_published", "source_id", "published_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    dedupe_key: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    completeness: Mapped[str] = mapped_column(String(16))
    fetch_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content_type: Mapped[str] = mapped_column(String(255))
    content_size: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot_key: Mapped[str] = mapped_column(Text)
    trust_tier_snapshot: Mapped[str] = mapped_column(String(8))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class DocumentCollectionState(Base):
    __tablename__ = "document_collection_states"
    __table_args__ = (
        CheckConstraint(
            "completeness IN ('complete', 'partial')",
            name="ck_document_collection_states_completeness",
        ),
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id"), primary_key=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    latest_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id"), nullable=False
    )
    completeness: Mapped[str] = mapped_column(String(16))
    fetch_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "parse_batch_id",
            "position",
            name="uq_document_chunks_batch_position",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    parse_batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_parse_batches.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict] = mapped_column(JSONB)
    embedding = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DocumentParseBatch(Base):
    __tablename__ = "document_parse_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'failed', 'unsupported')",
            name="ck_document_parse_batches_status",
        ),
        CheckConstraint(
            "chunk_count >= 0",
            name="ck_document_parse_batches_chunk_count",
        ),
        UniqueConstraint(
            "document_id",
            "processor_version",
            "config_fingerprint",
            "attempt_number",
            name="uq_document_parse_batches_attempt",
        ),
        Index(
            "ix_document_parse_batches_document_created",
            "document_id",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    processor_version: Mapped[str] = mapped_column(String(80))
    config_fingerprint: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentParseState(Base):
    __tablename__ = "document_parse_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'unsupported')",
            name="ck_document_parse_states_status",
        ),
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16))
    processor_version: Mapped[str] = mapped_column(String(80))
    config_fingerprint: Mapped[str] = mapped_column(String(64))
    latest_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_parse_batches.id"), nullable=True
    )
    current_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_parse_batches.id"), nullable=True
    )
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

