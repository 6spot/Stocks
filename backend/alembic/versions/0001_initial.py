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
            "trigger IN ('manual', 'schedule')",
            name="ck_task_definitions_trigger",
        ),
        sa.Column("strategy", sa.String(255)),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("config_snapshot", JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "task_schedules",
        sa.Column(
            "task_id",
            sa.String(120),
            sa.ForeignKey("task_definitions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("trigger_kind", sa.String(16), nullable=False),
        sa.Column("schedule_fingerprint", sa.String(64), nullable=False),
        sa.Column("schedule_snapshot", JSONB(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", JSONB()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "trigger_kind = 'schedule'",
            name="ck_task_schedules_trigger_kind",
        ),
    )
    op.create_index("ix_task_schedules_due", "task_schedules", ["next_due_at"])
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
        sa.Column(
            "triggered_by",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "retry_of_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_runs.id"),
        ),
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
        sa.Column("change_watermark_start", sa.BigInteger()),
        sa.Column("change_watermark_end", sa.BigInteger()),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.Column("scope_fingerprint", sa.String(64)),
        sa.Column("change_scan_progress", JSONB()),
        sa.Column("change_scan_completed", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_task_runs_status",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('manual', 'schedule', 'retry')",
            name="ck_task_runs_triggered_by",
        ),
        sa.CheckConstraint(
            "retry_of_run_id IS NULL OR retry_of_run_id != id",
            name="ck_task_runs_retry_distinct",
        ),
        sa.CheckConstraint(
            "status != 'running' OR process_token IS NOT NULL",
            name="ck_task_runs_running_owner",
        ),
        sa.CheckConstraint(
            "status NOT IN ('queued', 'succeeded', 'failed', 'cancelled') "
            "OR process_token IS NULL",
            name="ck_task_runs_closed_owner",
        ),
        sa.CheckConstraint(
            "(change_watermark_start IS NULL) = "
            "(change_watermark_end IS NULL)",
            name="ck_task_runs_change_watermark_pair",
        ),
        sa.CheckConstraint(
            "change_watermark_start IS NULL OR "
            "change_watermark_start <= change_watermark_end",
            name="ck_task_runs_change_watermark_order",
        ),
        sa.CheckConstraint(
            "(window_start IS NULL) = (window_end IS NULL)",
            name="ck_task_runs_window_time_pair",
        ),
        sa.CheckConstraint(
            "window_start IS NULL OR window_start <= window_end",
            name="ck_task_runs_window_time_order",
        ),
        sa.CheckConstraint(
            "scope_fingerprint IS NULL OR "
            "scope_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_task_runs_scope_fingerprint",
        ),
        sa.CheckConstraint(
            "change_scan_progress IS NULL OR "
            "jsonb_typeof(change_scan_progress) = 'object'",
            name="ck_task_runs_change_scan_progress",
        ),
        sa.CheckConstraint(
            "change_scan_completed IS NOT TRUE OR "
            "(change_watermark_end IS NOT NULL AND "
            "change_scan_progress IS NOT NULL AND "
            "change_scan_progress ->> 'status' = 'scanned')",
            name="ck_task_runs_scan_completed_requires_scan",
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
    op.create_index("ix_task_runs_status_created", "task_runs", ["status", "created_at"])
    op.create_index(
        "ix_task_runs_scope_sources",
        "task_runs",
        ["scope"],
        postgresql_using="gin",
        postgresql_ops={"scope": "jsonb_path_ops"},
    )
    op.create_table(
        "evidence_packages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selection_policy_version", sa.String(120), nullable=False),
        sa.Column("selection_budget", JSONB(), nullable=False),
        sa.Column("queries", JSONB(), nullable=False),
        sa.Column("selected_chunks", JSONB(), nullable=False),
        sa.Column(
            "evidence_groups",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("coverage", JSONB(), nullable=False),
        sa.Column("candidates_discovered", sa.Integer()),
        sa.Column("candidates_screened", sa.Integer()),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("unexamined_count", sa.Integer()),
        sa.Column("retrieval_truncated", sa.Boolean(), nullable=False),
        sa.Column("change_scan", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "selection_policy_version ~ '^[A-Za-z0-9._-]+$'",
            name="ck_evidence_packages_policy_version",
        ),
        sa.CheckConstraint(
            "(selection_budget ->> 'max_tokens')::integer > 0",
            name="ck_evidence_packages_max_tokens",
        ),
        sa.UniqueConstraint(
            "run_id",
            name="uq_evidence_packages_run",
        ),
        sa.CheckConstraint(
            "candidates_discovered IS NULL "
            "OR candidates_discovered >= 0",
            name="ck_evidence_packages_discovered",
        ),
        sa.CheckConstraint(
            "candidates_screened IS NULL "
            "OR candidates_screened >= 0",
            name="ck_evidence_packages_screened",
        ),
        sa.CheckConstraint(
            "selected_count >= 0",
            name="ck_evidence_packages_selected_count",
        ),
        sa.CheckConstraint(
            "unexamined_count IS NULL OR unexamined_count >= 0",
            name="ck_evidence_packages_unexamined_count",
        ),
    )
    op.create_table(
        "analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", name="uq_analyses_run"),
        sa.Column(
            "evidence_package_id",
            UUID(as_uuid=True),
            sa.ForeignKey("evidence_packages.id"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("findings", JSONB(), nullable=False),
        sa.Column("open_questions", JSONB(), nullable=False),
        sa.Column("evidence_refs", JSONB(), nullable=False),
        sa.Column("coverage", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(findings) = 'array'",
            name="ck_analyses_findings_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(open_questions) = 'array'",
            name="ck_analyses_open_questions_array",
        ),
    )
    op.create_table(
        "reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", name="uq_reports_run"),
        sa.Column(
            "analysis_id",
            UUID(as_uuid=True),
            sa.ForeignKey("analyses.id"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(120),
            sa.ForeignKey("task_definitions.id"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reports_task_created", "reports", ["task_id", "created_at"])
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
        sa.Column("classification", JSONB()),
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
    op.create_table(
        "document_processing_states",
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("stage", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("auto_retry", sa.Boolean(), nullable=False),
        sa.Column("reprocess_requested", sa.Boolean(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("owner_token", sa.String(200)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", JSONB()),
        sa.Column("wait_reason", sa.String(120)),
        sa.Column("skip_reason", sa.String(120)),
        sa.Column("processor_version", sa.String(80)),
        sa.Column("config_fingerprint", sa.String(64)),
        sa.Column(
            "input_batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_parse_batches.id"),
        ),
        sa.Column("applicability", JSONB()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "stage IN ('parse', 'classify', 'facts', 'index')",
            name="ck_document_processing_states_stage",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_document_processing_states_status",
        ),
        sa.CheckConstraint(
            "status != 'running' OR owner_token IS NOT NULL",
            name="ck_document_processing_states_running_owner",
        ),
        sa.CheckConstraint(
            "status = 'running' OR owner_token IS NULL",
            name="ck_document_processing_states_closed_owner",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1",
            name="ck_document_processing_states_attempts",
        ),
        sa.CheckConstraint(
            "status != 'failed' OR error IS NOT NULL",
            name="ck_document_processing_states_failed_error",
        ),
        sa.CheckConstraint(
            "status != 'skipped' OR skip_reason IS NOT NULL",
            name="ck_document_processing_states_skipped_reason",
        ),
    )
    op.create_index(
        "ix_document_processing_states_ready",
        "document_processing_states",
        ["stage", "status", "next_attempt_at"],
    )
    op.create_table(
        "facts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_key", sa.String(200), nullable=False),
        sa.Column("fact_type", sa.String(80), nullable=False),
        sa.Column("origin_method", sa.String(24), nullable=False),
        sa.Column("evidence_ref", JSONB(), nullable=False),
        sa.Column("subject_name", sa.String(255)),
        sa.Column("symbol", sa.String(32)),
        sa.Column("metric_id", sa.String(120)),
        sa.Column("statement", sa.Text()),
        sa.Column("value", sa.Float()),
        sa.Column("unit", sa.String(80)),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column(
            "attributes",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("processor_version", sa.String(80)),
        sa.Column("config_fingerprint", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "origin_method IN ('llm_extraction', 'source_mapping')",
            name="ck_facts_origin_method",
        ),
        sa.CheckConstraint(
            "statement IS NOT NULL OR value IS NOT NULL",
            name="ck_facts_content_present",
        ),
        sa.CheckConstraint(
            "value IS NULL OR unit IS NOT NULL",
            name="ck_facts_value_has_unit",
        ),
        sa.CheckConstraint(
            "period_start IS NULL OR period_end IS NULL "
            "OR period_start <= period_end",
            name="ck_facts_period_order",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_ref) = 'object'",
            name="ck_facts_evidence_ref_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes) = 'object'",
            name="ck_facts_attributes_object",
        ),
        sa.UniqueConstraint(
            "document_id",
            "fact_key",
            name="uq_facts_document_key",
        ),
    )
    op.create_index("ix_facts_document", "facts", ["document_id"])
    op.create_index("ix_facts_type_subject", "facts", ["fact_type", "subject_name"])
    op.create_index("ix_facts_symbol_metric", "facts", ["symbol", "metric_id"])
    op.create_index("ix_facts_period", "facts", ["period_start", "period_end"])
    op.create_table(
        "change_counters",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("watermark", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("watermark >= 0", name="ck_change_counters_watermark"),
    )
    op.create_table(
        "change_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("watermark", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('document_ingested', 'document_parsed', "
            "'document_classified', 'document_indexed', "
            "'fact_mapped', 'fact_extracted')",
            name="ck_change_records_kind",
        ),
        sa.UniqueConstraint("watermark", name="uq_change_records_watermark"),
    )
    op.create_index(
        "ix_change_records_document", "change_records", ["document_id", "watermark"]
    )
    op.create_index(
        "ix_change_records_kind_watermark", "change_records", ["kind", "watermark"]
    )
    op.create_table(
        "research_cursors",
        sa.Column(
            "task_id",
            sa.String(120),
            sa.ForeignKey("task_definitions.id"),
            primary_key=True,
        ),
        sa.Column("scope_fingerprint", sa.String(64), primary_key=True),
        sa.Column("watermark", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_research_cursors_scope_fingerprint",
        ),
        sa.CheckConstraint(
            "watermark >= 0", name="ck_research_cursors_watermark"
        ),
    )
    op.create_index("ix_research_cursors_task", "research_cursors", ["task_id"])


def downgrade():
    op.drop_index(
        "ix_document_processing_states_ready",
        table_name="document_processing_states",
    )
    op.drop_index("ix_research_cursors_task", table_name="research_cursors")
    op.drop_table("research_cursors")
    op.drop_index("ix_change_records_kind_watermark", table_name="change_records")
    op.drop_index("ix_change_records_document", table_name="change_records")
    op.drop_table("change_records")
    op.drop_table("change_counters")
    op.drop_index("ix_facts_period", table_name="facts")
    op.drop_index("ix_facts_symbol_metric", table_name="facts")
    op.drop_index("ix_facts_type_subject", table_name="facts")
    op.drop_index("ix_facts_document", table_name="facts")
    op.drop_table("facts")
    op.drop_table("document_processing_states")
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
    op.drop_index("ix_reports_task_created", table_name="reports")
    op.drop_table("reports")
    op.drop_table("analyses")
    op.drop_table("evidence_packages")
    op.drop_index("uq_task_runs_one_active", table_name="task_runs")
    op.drop_index("ix_task_runs_scope_sources", table_name="task_runs")
    op.drop_index("ix_task_runs_status_created", table_name="task_runs")
    op.drop_index("ix_task_runs_task_created", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_index("ix_task_schedules_due", table_name="task_schedules")
    op.drop_table("task_schedules")
    op.drop_table("task_definitions")
    op.drop_table("sources")
