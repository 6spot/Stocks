"""Initial intelligence storage.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("sources",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connector", sa.String(40), nullable=False),
        sa.Column("publisher", sa.String(255)),
        sa.Column("trust_tier", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_table("documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", sa.String(120), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("summary", sa.Text()),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("fetch_error", JSONB()),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("content_size", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_key", sa.Text(), nullable=False),
        sa.Column("trust_tier_snapshot", sa.String(8), nullable=False),
        sa.Column("extra", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "completeness IN ('complete', 'partial')",
            name="ck_documents_completeness",
        ),
        sa.UniqueConstraint(
            "source_id",
            "dedupe_key",
            "content_hash",
            name="uq_documents_source_content",
        ),
        sa.UniqueConstraint(
            "source_id",
            "dedupe_key",
            "version",
            name="uq_documents_source_version",
        ))
    op.create_index("ix_documents_collected", "documents", ["collected_at"])
    op.create_index("ix_documents_source_published", "documents", ["source_id", "published_at"])
    op.create_table(
        "document_collection_states",
        sa.Column("source_id", sa.String(120), sa.ForeignKey("sources.id"), primary_key=True),
        sa.Column("dedupe_key", sa.String(255), primary_key=True),
        sa.Column("latest_document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("fetch_error", JSONB()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "completeness IN ('complete', 'partial')",
            name="ck_document_collection_states_completeness",
        ),
    )
    op.create_table(
        "document_parse_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processor_version", sa.String(80), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", JSONB()),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('completed', 'failed', 'unsupported')",
            name="ck_document_parse_batches_status",
        ),
        sa.CheckConstraint(
            "chunk_count >= 0",
            name="ck_document_parse_batches_chunk_count",
        ),
        sa.UniqueConstraint(
            "document_id",
            "processor_version",
            "config_fingerprint",
            "attempt_number",
            name="uq_document_parse_batches_attempt",
        ),
    )
    op.create_index(
        "ix_document_parse_batches_document_created",
        "document_parse_batches",
        ["document_id", "created_at"],
    )
    op.create_table(
        "document_parse_states",
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("processor_version", sa.String(80), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("latest_batch_id", UUID(as_uuid=True), sa.ForeignKey("document_parse_batches.id")),
        sa.Column("current_batch_id", UUID(as_uuid=True), sa.ForeignKey("document_parse_batches.id")),
        sa.Column("error", JSONB()),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'unsupported')",
            name="ck_document_parse_states_status",
        ),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parse_batch_id", UUID(as_uuid=True), sa.ForeignKey("document_parse_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", JSONB(), nullable=False),
        sa.Column("embedding", Vector()),
        sa.Column("embedding_model", sa.String(255)),
        sa.Column("embedding_dimensions", sa.Integer()),
        sa.UniqueConstraint(
            "parse_batch_id",
            "position",
            name="uq_document_chunks_batch_position",
        ),
    )


def downgrade():
    op.drop_table("document_chunks")
    op.drop_table("document_parse_states")
    op.drop_index("ix_document_parse_batches_document_created", table_name="document_parse_batches")
    op.drop_table("document_parse_batches")
    op.drop_table("document_collection_states")
    op.drop_index("ix_documents_source_published", table_name="documents")
    op.drop_index("ix_documents_collected", table_name="documents")
    op.drop_table("documents")
    op.drop_table("sources")
