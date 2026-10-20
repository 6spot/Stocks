"""Initial intelligence storage.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from pgvector.sqlalchemy import Vector

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
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("summary", sa.Text()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_key", sa.Text(), nullable=False),
        sa.Column("trust_tier_snapshot", sa.String(8), nullable=False),
        sa.Column("extra", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("source_id", "dedupe_key", name="uq_documents_source_dedupe"))
    op.create_index("ix_documents_collected", "documents", ["collected_at"])
    op.create_index("ix_documents_source_published", "documents", ["source_id", "published_at"])
    op.create_table("document_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector()),
        sa.Column("embedding_model", sa.String(255)),
        sa.Column("embedding_dimensions", sa.Integer()),
        sa.UniqueConstraint("document_id", "position", name="uq_document_chunks_position"))

def downgrade():
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_source_published", table_name="documents")
    op.drop_index("ix_documents_collected", table_name="documents")
    op.drop_table("documents")
    op.drop_table("sources")
