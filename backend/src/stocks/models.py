from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from stocks.embedding_space import VECTOR_DIMENSIONS

# Ordered background processing stages for one Document content version. The
# vocabulary lives here because it is a persisted contract, not a policy.
PROCESSING_STAGES = ("parse", "classify", "facts", "index")
PROCESSING_STAGE_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
)


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


class TaskDefinition(Base):
    __tablename__ = "task_definitions"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('manual', 'schedule')",
            name="ck_task_definitions_trigger",
        ),
    )
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(default=True)
    strategy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[dict] = mapped_column(JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskSchedule(Base):
    """Persistent due state for a scheduled Task.

    A row exists only while the current configuration defines an enabled
    scheduled Task, so a removed, disabled or manual Task cannot keep firing
    from a leftover definition.
    """

    __tablename__ = "task_schedules"
    __table_args__ = (
        CheckConstraint(
            "trigger_kind = 'schedule'",
            name="ck_task_schedules_trigger_kind",
        ),
        Index("ix_task_schedules_due", "next_due_at"),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("task_definitions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    trigger_kind: Mapped[str] = mapped_column(String(16))
    schedule_fingerprint: Mapped[str] = mapped_column(String(64))
    schedule_snapshot: Mapped[dict] = mapped_column(JSONB)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_task_runs_status",
        ),
        CheckConstraint(
            "status != 'running' OR process_token IS NOT NULL",
            name="ck_task_runs_running_owner",
        ),
        CheckConstraint(
            "status NOT IN ('queued', 'succeeded', 'failed', 'cancelled') "
            "OR process_token IS NULL",
            name="ck_task_runs_closed_owner",
        ),
        Index(
            "uq_task_runs_one_active",
            "task_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_task_runs_task_created", "task_id", "created_at"),
        Index("ix_task_runs_status_created", "status", "created_at"),
        CheckConstraint(
            "triggered_by IN ('manual', 'schedule', 'retry')",
            name="ck_task_runs_triggered_by",
        ),
        CheckConstraint(
            "retry_of_run_id IS NULL OR retry_of_run_id != id",
            name="ck_task_runs_retry_distinct",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[str] = mapped_column(ForeignKey("task_definitions.id"))
    status: Mapped[str] = mapped_column(String(16))
    triggered_by: Mapped[str] = mapped_column(
        String(16), default="manual", server_default=text("'manual'")
    )
    retry_of_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_runs.id"), nullable=True
    )
    process_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    objective: Mapped[str] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[dict] = mapped_column(JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB)
    model_snapshot: Mapped[dict] = mapped_column(JSONB)
    prompt_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EvidencePackage(Base):
    __tablename__ = "evidence_packages"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_evidence_packages_run"),
        CheckConstraint(
            "selection_policy_version ~ '^[A-Za-z0-9._-]+$'",
            name="ck_evidence_packages_policy_version",
        ),
        CheckConstraint(
            "(selection_budget ->> 'max_tokens')::integer > 0",
            name="ck_evidence_packages_max_tokens",
        ),
        CheckConstraint(
            "candidates_discovered IS NULL "
            "OR candidates_discovered >= 0",
            name="ck_evidence_packages_discovered",
        ),
        CheckConstraint(
            "candidates_screened IS NULL "
            "OR candidates_screened >= 0",
            name="ck_evidence_packages_screened",
        ),
        CheckConstraint(
            "selected_count >= 0",
            name="ck_evidence_packages_selected_count",
        ),
        CheckConstraint(
            "unexamined_count IS NULL OR unexamined_count >= 0",
            name="ck_evidence_packages_unexamined_count",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    objective: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict] = mapped_column(JSONB)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    selection_policy_version: Mapped[str] = mapped_column(String(120))
    selection_budget: Mapped[dict] = mapped_column(JSONB)
    queries: Mapped[list] = mapped_column(JSONB)
    selected_chunks: Mapped[list] = mapped_column(JSONB)
    coverage: Mapped[dict] = mapped_column(JSONB)
    candidates_discovered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidates_screened: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_count: Mapped[int] = mapped_column(Integer)
    unexamined_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_truncated: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_analyses_run"),
        CheckConstraint(
            "jsonb_typeof(findings) = 'array'",
            name="ck_analyses_findings_array",
        ),
        CheckConstraint(
            "jsonb_typeof(open_questions) = 'array'",
            name="ck_analyses_open_questions_array",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    evidence_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence_packages.id"),
    )
    summary: Mapped[str] = mapped_column(Text)
    findings: Mapped[list] = mapped_column(JSONB)
    open_questions: Mapped[list] = mapped_column(JSONB)
    evidence_refs: Mapped[list] = mapped_column(JSONB)
    coverage: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_reports_run"),
        Index("ix_reports_task_created", "task_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    analysis_id: Mapped[UUID] = mapped_column(
        ForeignKey("analyses.id")
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("task_definitions.id")
    )
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskStepResult(Base):
    __tablename__ = "task_step_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'skipped', 'cancelled')",
            name="ck_task_step_results_status",
        ),
        UniqueConstraint(
            "run_id",
            "step_id",
            name="uq_task_step_results_run_step",
        ),
        UniqueConstraint(
            "run_id",
            "position",
            name="uq_task_step_results_run_position",
        ),
        Index("ix_task_step_results_run_created", "run_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    step_id: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16))
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evidence_refs: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    usage: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_model_calls_status",
        ),
        CheckConstraint(
            "duration_ms >= 0",
            name="ck_model_calls_duration",
        ),
        UniqueConstraint(
            "run_id",
            "step_id",
            "attempt_number",
            name="uq_model_calls_run_step_attempt",
        ),
        Index("ix_model_calls_run_created", "run_id", "created_at"),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_model_calls_prompt_tokens",
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_model_calls_completion_tokens",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_model_calls_total_tokens",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    step_id: Mapped[str] = mapped_column(String(120))
    attempt_number: Mapped[int] = mapped_column(Integer)
    requested_alias: Mapped[str] = mapped_column(String(120))
    attempt_alias: Mapped[str] = mapped_column(String(120))
    requested_model_name: Mapped[str] = mapped_column(String(255))
    attempt_model_name: Mapped[str] = mapped_column(String(255))
    reported_model_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16))
    duration_ms: Mapped[int] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    # Current classification of this content version. One JSONB column, no
    # classification history table: an explicit reclassification rewrites it.
    # ``None`` means no classification result exists yet; the queue/attempt state
    # lives in ``document_processing_states`` for the ``classify`` stage.
    classification: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class DocumentChunkEmbedding(Base):
    __tablename__ = "document_chunk_embeddings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "chunk_id",
            "model_name",
            "dimensions",
            name="pk_document_chunk_embeddings",
        ),
        CheckConstraint(
            f"dimensions = {VECTOR_DIMENSIONS}",
            name="ck_document_chunk_embeddings_dimensions",
        ),
        Index(
            "ix_document_chunk_embeddings_space",
            "model_name",
            "dimensions",
        ),
        Index(
            "ix_document_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE")
    )
    model_alias: Mapped[str] = mapped_column(String(120))
    model_name: Mapped[str] = mapped_column(String(255))
    dimensions: Mapped[int] = mapped_column(Integer)
    embedding = mapped_column(Vector(VECTOR_DIMENSIONS), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentEmbeddingState(Base):
    __tablename__ = "document_embedding_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_document_embedding_states_status",
        ),
        CheckConstraint(
            "chunk_count >= 0",
            name="ck_document_embedding_states_chunk_count",
        ),
        CheckConstraint(
            "indexed_count >= 0 AND indexed_count <= chunk_count",
            name="ck_document_embedding_states_indexed_count",
        ),
        CheckConstraint(
            f"dimensions = {VECTOR_DIMENSIONS}",
            name="ck_document_embedding_states_dimensions",
        ),
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16))
    parse_batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_parse_batches.id", ondelete="CASCADE")
    )
    model_alias: Mapped[str] = mapped_column(String(120))
    model_name: Mapped[str] = mapped_column(String(255))
    dimensions: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    indexed_count: Mapped[int] = mapped_column(Integer)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentProcessingState(Base):
    """Current background-processing record for one Document stage.

    One row per ``(document_id, stage)`` holds the queue/attempt/ownership state
    only. Immutable parse batches, parse state pointers and embedding coverage
    stay in their own tables, so a stage completion never rewrites a stored
    result and a failed reprocess never revokes a usable one.
    """

    __tablename__ = "document_processing_states"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('parse', 'classify', 'facts', 'index')",
            name="ck_document_processing_states_stage",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_document_processing_states_status",
        ),
        CheckConstraint(
            "status != 'running' OR owner_token IS NOT NULL",
            name="ck_document_processing_states_running_owner",
        ),
        CheckConstraint(
            "status = 'running' OR owner_token IS NULL",
            name="ck_document_processing_states_closed_owner",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1",
            name="ck_document_processing_states_attempts",
        ),
        CheckConstraint(
            "status != 'failed' OR error IS NOT NULL",
            name="ck_document_processing_states_failed_error",
        ),
        CheckConstraint(
            "status != 'skipped' OR skip_reason IS NOT NULL",
            name="ck_document_processing_states_skipped_reason",
        ),
        Index(
            "ix_document_processing_states_ready",
            "stage",
            "status",
            "next_attempt_at",
        ),
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    stage: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    auto_retry: Mapped[bool] = mapped_column(Boolean, default=True)
    reprocess_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    wait_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    processor_version: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    config_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    input_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_parse_batches.id"), nullable=True
    )
    applicability: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

