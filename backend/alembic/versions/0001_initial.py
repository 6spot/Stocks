"""Initial intelligence storage.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID
from stocks.embedding_space import VECTOR_DIMENSIONS

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
    op.create_table(
        "task_definitions",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "trigger = 'manual'",
            name="ck_task_definitions_manual_trigger",
        ),
        sa.Column("strategy", sa.String(255)),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("config_snapshot", JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "task_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(120),
            sa.ForeignKey("task_definitions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("process_token", sa.String(200)),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(255)),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("config_snapshot", JSONB(), nullable=False),
        sa.Column("model_snapshot", JSONB(), nullable=False),
        sa.Column("prompt_snapshot", JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("output", JSONB()),
        sa.Column("error", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_task_runs_status",
        ),
        sa.CheckConstraint(
            "status != 'running' OR process_token IS NOT NULL",
            name="ck_task_runs_running_owner",
        ),
        sa.CheckConstraint(
            "status NOT IN ('queued', 'succeeded', 'failed') "
            "OR process_token IS NULL",
            name="ck_task_runs_closed_owner",
        ),
    )
    op.create_index(
        "uq_task_runs_one_active",
        "task_runs",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_task_runs_task_created", "task_runs", ["task_id", "created_at"])
    op.create_table(
        "task_step_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", JSONB()),
        sa.Column("evidence_refs", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("usage", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'skipped', 'cancelled')",
            name="ck_task_step_results_status",
        ),
        sa.UniqueConstraint(
            "run_id",
            "step_id",
            name="uq_task_step_results_run_step",
        ),
        sa.UniqueConstraint(
            "run_id",
            "position",
            name="uq_task_step_results_run_position",
        ),
    )
    op.create_index(
        "ix_task_step_results_run_created",
        "task_step_results",
        ["run_id", "created_at"],
    )
    op.create_table(
        "model_calls",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(120), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("requested_alias", sa.String(120), nullable=False),
        sa.Column("attempt_alias", sa.String(120), nullable=False),
        sa.Column("requested_model_name", sa.String(255), nullable=False),
        sa.Column("attempt_model_name", sa.String(255), nullable=False),
        sa.Column("reported_model_name", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("fallback_reason", sa.String(80)),
        sa.Column("error", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_model_calls_status",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="ck_model_calls_duration",
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_model_calls_prompt_tokens",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_model_calls_completion_tokens",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_model_calls_total_tokens",
        ),
        sa.UniqueConstraint(
            "run_id",
            "step_id",
            "attempt_number",
            name="uq_model_calls_run_step_attempt",
        ),
    )
    op.create_index("ix_model_calls_run_created", "model_calls", ["run_id", "created_at"])
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
        sa.UniqueConstraint(
            "parse_batch_id",
            "position",
            name="uq_document_chunks_batch_position",
        ),
    )
    op.create_table(
        "document_chunk_embeddings",
        sa.Column(
            "chunk_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_alias", sa.String(120), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(VECTOR_DIMENSIONS), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "chunk_id",
            "model_name",
            "dimensions",
            name="pk_document_chunk_embeddings",
        ),
        sa.CheckConstraint(
            f"dimensions = {VECTOR_DIMENSIONS}",
            name="ck_document_chunk_embeddings_dimensions",
        ),
    )
    op.create_index(
        "ix_document_chunk_embeddings_space",
        "document_chunk_embeddings",
        ["model_name", "dimensions"],
    )
    op.create_index(
        "ix_document_chunk_embeddings_hnsw",
        "document_chunk_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "document_embedding_states",
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "parse_batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_parse_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_alias", sa.String(120), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("indexed_count", sa.Integer(), nullable=False),
        sa.Column("error", JSONB()),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_document_embedding_states_status",
        ),
        sa.CheckConstraint(
            "chunk_count >= 0",
            name="ck_document_embedding_states_chunk_count",
        ),
        sa.CheckConstraint(
            "indexed_count >= 0 AND indexed_count <= chunk_count",
            name="ck_document_embedding_states_indexed_count",
        ),
        sa.CheckConstraint(
            f"dimensions = {VECTOR_DIMENSIONS}",
            name="ck_document_embedding_states_dimensions",
        ),
    )


def downgrade():
    op.drop_table("document_embedding_states")
    op.drop_index("ix_document_chunk_embeddings_hnsw", table_name="document_chunk_embeddings")
    op.drop_index("ix_document_chunk_embeddings_space", table_name="document_chunk_embeddings")
    op.drop_table("document_chunk_embeddings")
    op.drop_table("document_chunks")
    op.drop_table("document_parse_states")
    op.drop_index("ix_document_parse_batches_document_created", table_name="document_parse_batches")
    op.drop_table("document_parse_batches")
    op.drop_table("document_collection_states")
    op.drop_index("ix_documents_source_published", table_name="documents")
    op.drop_index("ix_documents_collected", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_model_calls_run_created", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index("ix_task_step_results_run_created", table_name="task_step_results")
    op.drop_table("task_step_results")
    op.drop_index("uq_task_runs_one_active", table_name="task_runs")
    op.drop_index("ix_task_runs_task_created", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_table("task_definitions")
    op.drop_table("sources")
