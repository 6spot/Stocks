from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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
        Index(
            "ix_task_runs_scope_sources",
            "scope",
            postgresql_using="gin",
            postgresql_ops={"scope": "jsonb_path_ops"},
        ),
        CheckConstraint(
            "triggered_by IN ('manual', 'schedule', 'retry')",
            name="ck_task_runs_triggered_by",
        ),
        CheckConstraint(
            "retry_of_run_id IS NULL OR retry_of_run_id != id",
            name="ck_task_runs_retry_distinct",
        ),
        CheckConstraint(
            "(change_watermark_start IS NULL) = "
            "(change_watermark_end IS NULL)",
            name="ck_task_runs_change_watermark_pair",
        ),
        CheckConstraint(
            "change_watermark_start IS NULL OR "
            "change_watermark_start <= change_watermark_end",
            name="ck_task_runs_change_watermark_order",
        ),
        CheckConstraint(
            "(window_start IS NULL) = (window_end IS NULL)",
            name="ck_task_runs_window_time_pair",
        ),
        CheckConstraint(
            "window_start IS NULL OR window_start <= window_end",
            name="ck_task_runs_window_time_order",
        ),
        CheckConstraint(
            "scope_fingerprint IS NULL OR "
            "scope_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_task_runs_scope_fingerprint",
        ),
        CheckConstraint(
            "change_scan_progress IS NULL OR "
            "jsonb_typeof(change_scan_progress) = 'object'",
            name="ck_task_runs_change_scan_progress",
        ),
        CheckConstraint(
            "change_scan_completed IS NOT TRUE OR "
            "(change_watermark_end IS NOT NULL AND "
            "change_scan_progress IS NOT NULL AND "
            "change_scan_progress ->> 'status' = 'scanned')",
            name="ck_task_runs_scan_completed_requires_scan",
        ),
        CheckConstraint(
            "jsonb_typeof(research_inputs) = 'array'",
            name="ck_task_runs_research_inputs_array",
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
    # The incremental change window this Run scanned, fixed when execution starts so
    # a retry or a later Run cannot silently widen or narrow it. ``None`` on a
    # collection Run, which does not use research windows.
    change_watermark_start: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    change_watermark_end: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Versioned raw page position and scan state. This holds references and
    # counts only; source text remains in the Repository and Evidence Package.
    # ``none_as_null`` keeps "no scan state yet" as SQL NULL rather than the JSON
    # value ``null``, so the column constraint and the resume query agree.
    change_scan_progress: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    # Set only when the window scan finished; the cursor advances with this, in the
    # same transaction as the saved Analysis and Report.
    change_scan_completed: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    # Append-only ledger of the material this Run actually used beyond the
    # initial Evidence Package snapshot: Tool-provided originals registered in
    # the Repository for this Run, and the bounded gaps of attempts that produced
    # nothing citable. ``selected_chunks``/``evidence_groups``/``coverage`` stay
    # the frozen selection snapshot and are never rewritten.
    research_inputs: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
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
    # Every selected readable Chunk, kept as the citation unit a Report repeats.
    selected_chunks: Mapped[list] = mapped_column(JSONB)
    # V1 merged candidates grouped by Document version. One group holds the
    # Chunks and Facts that belong to the same evidence chain, so a Document and
    # its Fact are never counted as independent support.
    evidence_groups: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # V2-03 same-metric comparisons across every selected Document: differing
    # values, differing bases and differing periods side by side. Nothing here is
    # averaged or marked resolved, so a report can always show both numbers.
    metric_comparisons: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    coverage: Mapped[dict] = mapped_column(JSONB)
    candidates_discovered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidates_screened: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_count: Mapped[int] = mapped_column(Integer)
    unexamined_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_truncated: Mapped[bool] = mapped_column(Boolean)
    # Snapshot of the fixed change window and raw page position represented by
    # this package. It remains None for nonincremental selection callers.
    change_scan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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
        CheckConstraint(
            "jsonb_typeof(research_inputs) = 'array'",
            name="ck_analyses_research_inputs_array",
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
    # Frozen copy of the Run's research-input ledger at the moment this Analysis
    # was saved, so a later Run or Document change cannot alter what a published
    # report used as its actual inputs.
    research_inputs: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
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
        CheckConstraint(
            "call_purpose IN ('research', 'tool_plan')",
            name="ck_model_calls_purpose",
        ),
        UniqueConstraint(
            "run_id",
            "step_id",
            "call_purpose",
            "attempt_number",
            name="uq_model_calls_run_step_purpose_attempt",
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
    # Which call of the step this attempt belongs to. One Agent step can make a
    # bounded Tool-planning call before its single research call, and both must
    # stay traceable without colliding on their attempt numbers.
    call_purpose: Mapped[str] = mapped_column(
        String(16), default="research", server_default=text("'research'")
    )
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


FACT_ORIGIN_METHODS = ("llm_extraction", "source_mapping")


class Fact(Base):
    """A reusable structured statement derived from one Document version.

    One generic table serves both text extraction and API field mapping. Nothing
    here is a per-metric table: kind-specific detail lives in ``attributes``.

    Every Fact cites the exact Document version it came from and an
    ``evidence_ref`` that points at either a stored Chunk or a JSON field path, so
    a reader can always get back to the original bytes. A revised value is a new
    Fact on a new Document version; an existing Fact is never overwritten, which
    is what keeps an older report's citation valid.
    """

    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint(
            "origin_method IN ('llm_extraction', 'source_mapping')",
            name="ck_facts_origin_method",
        ),
        CheckConstraint(
            "statement IS NOT NULL OR value IS NOT NULL",
            name="ck_facts_content_present",
        ),
        CheckConstraint(
            "value IS NULL OR unit IS NOT NULL",
            name="ck_facts_value_has_unit",
        ),
        CheckConstraint(
            "period_start IS NULL OR period_end IS NULL "
            "OR period_start <= period_end",
            name="ck_facts_period_order",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_ref) = 'object'",
            name="ck_facts_evidence_ref_object",
        ),
        CheckConstraint(
            "jsonb_typeof(attributes) = 'object'",
            name="ck_facts_attributes_object",
        ),
        # Idempotency: the same extracted statement or the same mapped field of
        # the same content version can exist only once, so a retried attempt
        # cannot multiply identical Facts.
        UniqueConstraint(
            "document_id",
            "fact_key",
            name="uq_facts_document_key",
        ),
        Index("ix_facts_document", "document_id"),
        Index("ix_facts_type_subject", "fact_type", "subject_name"),
        Index("ix_facts_symbol_metric", "symbol", "metric_id"),
        Index("ix_facts_period", "period_start", "period_end"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    # Deterministic identity of this Fact within its Document version, derived
    # from the origin method, the mapped field path or the extracted statement.
    fact_key: Mapped[str] = mapped_column(String(200))
    fact_type: Mapped[str] = mapped_column(String(80))
    origin_method: Mapped[str] = mapped_column(String(24))
    evidence_ref: Mapped[dict] = mapped_column(JSONB)
    subject_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metric_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the source made this value publicly knowable, not when Stocks stored it.
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    processor_version: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    config_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))



class ChangeCounter(Base):
    """A single row holding the committed-visible change watermark.

    Incrementing this counter takes a row lock that is held until the enclosing
    transaction commits. Two writers therefore serialize on it: a transaction that
    started earlier but commits later gets the higher watermark, and any reader that
    observes watermark N knows every change with a lower watermark is already
    committed. That property is what makes a research window不漏数 — a plain
    timestamp or a sequence value allocated at statement time does not have it,
    because a sequence number is handed out before the row is visible.
    """

    __tablename__ = "change_counters"
    __table_args__ = (
        CheckConstraint("watermark >= 0", name="ck_change_counters_watermark"),
    )
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    watermark: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChangeRecord(Base):
    """One research-visible change, ordered by its committed watermark.

    Written in the same transaction as the change it describes, so a record and the
    Document/Fact state it refers to become visible together. `kind` says what
    changed; `document_id` is the version the change applies to. A Fact change keeps
    its own `fact_id` so a revision can be attributed without rewriting the
    Document.
    """

    __tablename__ = "change_records"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('document_ingested', 'document_parsed', "
            "'document_classified', 'document_indexed', "
            "'fact_mapped', 'fact_extracted')",
            name="ck_change_records_kind",
        ),
        UniqueConstraint("watermark", name="uq_change_records_watermark"),
        Index("ix_change_records_document", "document_id", "watermark"),
        Index("ix_change_records_kind_watermark", "kind", "watermark"),
    )
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    watermark: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    fact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchCursor(Base):
    """Per Task + scope watermark of the last completed incremental scan.

    Keyed by a deterministic fingerprint of the Task and its research scope, so two
    Tasks researching the same material keep independent progress and a Task whose
    scope changes starts from a fresh row instead of inheriting the old range.
    """

    __tablename__ = "research_cursors"
    __table_args__ = (
        CheckConstraint(
            "scope_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_research_cursors_scope_fingerprint",
        ),
        CheckConstraint(
            "watermark >= 0", name="ck_research_cursors_watermark"
        ),
        Index("ix_research_cursors_task", "task_id"),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("task_definitions.id"), primary_key=True
    )
    scope_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    watermark: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
