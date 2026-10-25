import { z } from "zod";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const jsonObject = z.record(z.string(), z.unknown());

export const sessionPayloadSchema = z
  .object({
    authenticated: z.literal(true),
    session: z.object({
      issued_at: z.string(),
      expires_at: z.string(),
      csrf_token: z.string(),
    }),
  })
  .passthrough();

export type SessionPayload = z.infer<typeof sessionPayloadSchema>;

export const apiErrorSchema = z
  .object({
    code: z.string().optional(),
    message: z.string().optional(),
    detail: z
      .union([
        z.string(),
        z
          .object({
            code: z.string().optional(),
            message: z.string().optional(),
          })
          .passthrough(),
      ])
      .optional(),
  })
  .passthrough();

export const safeErrorSchema = z
  .object({
    code: z.string().optional(),
    message: z.string().optional(),
  })
  .passthrough()
  .nullable()
  .optional();

export type SafeError = z.infer<typeof safeErrorSchema>;

const pageSchema = <T extends z.ZodType>(item: T) =>
  z
    .object({
      items: z.array(item),
      next_cursor: nullableString,
      has_more: z.boolean(),
      limit: z.number().int().nonnegative(),
    })
    .passthrough();

const usageModelSchema = z
  .object({
    provider: nullableString,
    model_name: nullableString,
    calls: z.number().int().nonnegative(),
    total_tokens: nullableNumber,
    cost_usd: nullableNumber,
    cost_unknown_calls: z.number().int().nonnegative(),
  })
  .passthrough();

// Deterministic usage from stored ModelCall rows. cost_usd = null means the
// provider reported no cost, so the console shows it as unknown instead of 0.
export const usageSummarySchema = z
  .object({
    call_count: z.number().int().nonnegative(),
    succeeded_calls: z.number().int().nonnegative(),
    failed_calls: z.number().int().nonnegative(),
    fallback_calls: z.number().int().nonnegative(),
    prompt_tokens: nullableNumber,
    completion_tokens: nullableNumber,
    total_tokens: nullableNumber,
    duration_ms: nullableNumber,
    cost_usd: nullableNumber,
    cost_known_calls: z.number().int().nonnegative(),
    cost_unknown_calls: z.number().int().nonnegative(),
    models: z.array(usageModelSchema).optional(),
    models_truncated: z.boolean().optional(),
  })
  .passthrough();

export type UsageSummary = z.infer<typeof usageSummarySchema>;

export const changeScanSchema = z
  .object({
    change_watermark_start: z.number().nullable(),
    change_watermark_end: z.number().nullable(),
    window_start: nullableString,
    window_end: nullableString,
    change_scan_completed: z.boolean().nullable(),
    change_scan_progress: z.unknown(),
  })
  .passthrough();

export const runSummarySchema = z
  .object({
    run_id: z.string(),
    task_id: z.string(),
    status: z.string(),
    triggered_by: nullableString,
    retry_of_run_id: nullableString,
    objective: nullableString,
    strategy: nullableString,
    scope: z.unknown(),
    config_hash: nullableString,
    model_snapshot: z.unknown(),
    output: z.unknown(),
    error: safeErrorSchema,
    created_at: nullableString,
    started_at: nullableString,
    finished_at: nullableString,
    step_count: z.number().int().nonnegative().optional(),
    change_scan: changeScanSchema.nullable().optional(),
  })
  .passthrough();

export type RunSummary = z.infer<typeof runSummarySchema>;

export const sourceSummarySchema = z
  .object({
    source_id: z.string(),
    name: z.string(),
    connector: nullableString,
    publisher: nullableString,
    trust_tier: nullableString,
    enabled: z.boolean().nullable().optional(),
    configured: z.boolean(),
    persisted: z.boolean(),
    collection: z
      .object({
        status: z.string(),
        request_success: z.boolean().nullable().optional(),
        no_update: z.boolean().nullable().optional(),
        discovered: nullableNumber,
        ingested: nullableNumber,
        reused: nullableNumber,
        skipped: nullableNumber,
        failed: nullableNumber,
        error: safeErrorSchema,
        latest_attempt_at: nullableString,
        latest_document_id: nullableString,
        completeness: nullableString,
        last_attempt_error: safeErrorSchema,
        last_collection_run: runSummarySchema.nullable().optional(),
        processing_backlog: z
          .object({
            pending: z.number().int().nonnegative(),
            failed: z.number().int().nonnegative(),
          })
          .passthrough()
          .optional(),
      })
      .passthrough(),
    freshness: z
      .object({
        status: z.string(),
        expected_freshness: nullableString,
        age_seconds: nullableNumber,
        latest_disclosure_at: nullableString,
        latest_collected_at: nullableString,
        basis: nullableString,
      })
      .passthrough(),
  })
  .passthrough();

export type SourceSummary = z.infer<typeof sourceSummarySchema>;

export const sourcesResponseSchema = pageSchema(sourceSummarySchema).extend({
  configuration_error: safeErrorSchema,
});

export type SourcesResponse = z.infer<typeof sourcesResponseSchema>;

export const recentReportSchema = z
  .object({
    report_id: z.string(),
    run_id: z.string(),
    task_id: z.string(),
    title: z.string(),
    created_at: z.string(),
  })
  .passthrough();

export type RecentReport = z.infer<typeof recentReportSchema>;

export const processingStageSchema = z
  .object({
    stage: z.string(),
    status: z.string(),
    attempts: z.number().int().nonnegative(),
    wait_reason: nullableString,
    skip_reason: nullableString,
    error: safeErrorSchema,
  })
  .passthrough();

export type ProcessingStage = z.infer<typeof processingStageSchema>;

export const collectionStateSchema = z
  .object({
    source_id: z.string(),
    dedupe_key: z.string(),
    latest_document_id: z.string(),
    completeness: nullableString,
    fetch_error: safeErrorSchema,
    published_at: nullableString,
    source_updated_at: nullableString,
    last_attempted_at: nullableString,
  })
  .passthrough();

export type CollectionState = z.infer<typeof collectionStateSchema>;

export const parseStateSchema = z
  .object({
    status: z.string(),
    processor_version: nullableString,
    latest_batch_id: nullableString,
    current_batch_id: nullableString,
    chunk_count: z.number().int().nonnegative(),
    error: safeErrorSchema,
    attempted_at: nullableString,
  })
  .passthrough();

export type ParseState = z.infer<typeof parseStateSchema>;

export const embeddingStateSchema = z
  .object({
    status: z.string(),
    current: z.boolean(),
    parse_batch_id: nullableString,
    last_attempt_parse_batch_id: nullableString,
    model_alias: nullableString,
    model_name: nullableString,
    dimensions: nullableNumber,
    chunk_count: z.number().int().nonnegative(),
    indexed_count: z.number().int().nonnegative(),
    unindexed_count: z.number().int().nonnegative(),
    error: safeErrorSchema,
    attempted_at: nullableString,
  })
  .passthrough();

export type EmbeddingState = z.infer<typeof embeddingStateSchema>;

export const documentSummarySchema = z
  .object({
    id: z.string(),
    source_id: z.string(),
    dedupe_key: nullableString,
    version: z.number().int().positive(),
    title: z.string(),
    original_url: nullableString,
    published_at: nullableString,
    source_updated_at: nullableString,
    collected_at: z.string(),
    trust_tier: nullableString,
    completeness: z.string(),
    fetch_error: safeErrorSchema,
    content_type: nullableString,
    content_size: nullableNumber,
    content_hash: nullableString,
    snapshot_key: nullableString,
    classification: z.unknown(),
    processing_stages: z.array(processingStageSchema).optional(),
    collection_state: collectionStateSchema.nullable().optional(),
    parse_state: parseStateSchema.nullable().optional(),
    embedding_state: embeddingStateSchema.nullable().optional(),
  })
  .passthrough();

export type DocumentSummary = z.infer<typeof documentSummarySchema>;

export const documentsResponseSchema = pageSchema(documentSummarySchema);
export type DocumentsResponse = z.infer<typeof documentsResponseSchema>;

export const overviewSchema = z
  .object({
    collection: z
      .object({
        configured_sources: z.number().int().nonnegative(),
        partial_sources: z.array(sourceSummarySchema),
        source_errors: safeErrorSchema,
      })
      .passthrough(),
    processing: z
      .object({
        by_status: z.record(z.string(), z.number().int().nonnegative()),
        by_stage: z.record(z.string(), z.unknown()),
      })
      .passthrough(),
    processing_state: z
      .object({
        status: z.string(),
        pending: z.number().int().nonnegative(),
        running: z.number().int().nonnegative(),
        failed: z.number().int().nonnegative(),
      })
      .passthrough(),
    runs: z
      .object({
        active_count: z.number().int().nonnegative(),
        failed_count: z.number().int().nonnegative(),
        recent: z.array(runSummarySchema),
      })
      .passthrough(),
    reports: z.array(recentReportSchema),
    research: z
      .object({
        latest: z
          .object({
            report_id: z.string(),
            run_id: z.string(),
            task_id: z.string(),
            title: z.string(),
            created_at: nullableString,
            as_of: nullableString,
            candidates_discovered: nullableNumber,
            candidates_screened: nullableNumber,
            selected_count: z.number().int().nonnegative(),
            evidence_group_count: nullableNumber,
            origin_group_count: nullableNumber,
            merged_origin_group_count: nullableNumber,
            fact_count: nullableNumber,
            unexamined_count: nullableNumber,
            retrieval_truncated: z.boolean(),
            research_input_count: z.number().int().nonnegative(),
            gaps: z.array(z.string()),
          })
          .passthrough()
          .nullable(),
        usage: usageSummarySchema.extend({
          run_count: z.number().int().nonnegative(),
        }),
      })
      .passthrough(),
    recent_documents: z.array(documentSummarySchema),
    task_count: z.number().int().nonnegative(),
    configuration_errors: z
      .object({
        sources: safeErrorSchema,
        tasks: safeErrorSchema,
      })
      .passthrough(),
  })
  .passthrough();

export type Overview = z.infer<typeof overviewSchema>;

export const taskSchema = z
  .object({
    task_id: z.string(),
    name: z.string(),
    type: z.string(),
    trigger: z.unknown(),
    enabled: z.boolean(),
    strategy: nullableString,
    scope: z.unknown(),
    updated_at: nullableString,
    active_run: runSummarySchema.nullable(),
    last_run: runSummarySchema.nullable(),
    configured: z.boolean(),
    configuration: z.unknown(),
  })
  .passthrough();

export type TaskSummary = z.infer<typeof taskSchema>;

export const tasksResponseSchema = pageSchema(taskSchema).extend({
  configuration_error: safeErrorSchema,
});

export const runsResponseSchema = pageSchema(runSummarySchema);
export type RunsResponse = z.infer<typeof runsResponseSchema>;

export const stepSchema = z
  .object({
    step_id: z.string(),
    position: z.number().int(),
    type: z.string(),
    status: z.string(),
    output: z.unknown(),
    evidence_refs: z.unknown(),
    usage: z.unknown(),
    error: safeErrorSchema,
    created_at: nullableString,
  })
  .passthrough();

export const modelCallSchema = z
  .object({
    step_id: z.string(),
    attempt_number: z.number().int(),
    requested_alias: nullableString,
    attempt_alias: nullableString,
    requested_model_name: nullableString,
    attempt_model_name: nullableString,
    reported_model_name: nullableString,
    provider: nullableString,
    status: z.string(),
    duration_ms: nullableNumber,
    prompt_tokens: nullableNumber,
    completion_tokens: nullableNumber,
    total_tokens: nullableNumber,
    cost_usd: nullableNumber,
    fallback_reason: nullableString,
    error: safeErrorSchema,
    created_at: nullableString,
  })
  .passthrough();

export const failedStepSchema = z
  .object({
    step_id: z.string(),
    position: z.number().int(),
    type: z.string(),
    status: z.string(),
    error: safeErrorSchema,
  })
  .passthrough();

export const runDetailSchema = runSummarySchema
  .extend({
    configuration: z.unknown(),
    model_snapshot: z.unknown(),
    steps: z.array(stepSchema),
    model_calls: z.array(modelCallSchema),
    usage: usageSummarySchema,
    retry_of: z
      .object({
        run_id: z.string(),
        failed_steps: z.array(failedStepSchema),
      })
      .passthrough()
      .nullable(),
    evidence_package: z
      .object({
        evidence_package_id: z.string(),
        selected_count: z.number().int().nonnegative(),
        evidence_group_count: nullableNumber,
        coverage: z.unknown(),
      })
      .passthrough()
      .nullable(),
    report: z
      .object({
        report_id: z.string(),
        title: z.string(),
        created_at: nullableString,
      })
      .passthrough()
      .nullable(),
    actions: z
      .object({
        can_retry: z.boolean(),
        can_continue: z.boolean(),
        can_end: z.boolean(),
      })
      .passthrough(),
    failed_steps: z.array(failedStepSchema),
  })
  .passthrough();

export type RunDetail = z.infer<typeof runDetailSchema>;
export type RunStep = z.infer<typeof stepSchema>;
export type ModelCall = z.infer<typeof modelCallSchema>;

export const runMutationSchema = runSummarySchema.extend({
  created: z.boolean().optional(),
  continued_from: z.string().optional(),
  resumed_from: nullableString.optional(),
});

export const processingItemSchema = z
  .object({
    document_id: z.string(),
    stage: z.enum(["parse", "classify", "facts", "index"]),
    status: z.string(),
    attempts: z.number().int().nonnegative(),
    max_attempts: z.number().int().nonnegative(),
    auto_retry: z.boolean().optional(),
    reprocess_requested: z.boolean().optional(),
    next_attempt_at: nullableString,
    started_at: nullableString,
    finished_at: nullableString,
    error: safeErrorSchema,
    wait_reason: nullableString,
    skip_reason: nullableString,
    processor_version: nullableString,
    processor_available: z.boolean().optional(),
    document: documentSummarySchema.nullable().optional(),
  })
  .passthrough();

export type ProcessingItem = z.infer<typeof processingItemSchema>;

export const processingResponseSchema = z
  .object({
    items: z.array(processingItemSchema),
    next_cursor: nullableString,
    has_more: z.boolean(),
    limit: z.number().int().nonnegative(),
    available_stages: z.array(z.string()),
    counts: z
      .object({
        by_status: z.record(z.string(), z.number().int().nonnegative()),
        by_stage: z.record(z.string(), z.unknown()),
      })
      .passthrough(),
  })
  .passthrough();

export type ProcessingResponse = z.infer<typeof processingResponseSchema>;

export const processingRetrySchema = z
  .object({
    document_id: z.string(),
    accepted: z.boolean(),
    executions: z.array(
      z
        .object({
          document_id: nullableString,
          stage: z.string(),
          status: z.string(),
        })
        .passthrough(),
    ),
    errors: z.array(
      z
        .object({
          document_id: nullableString,
          stage: z.string(),
          error: safeErrorSchema,
        })
        .passthrough(),
    ),
  })
  .passthrough();

export type ProcessingRetryResult = z.infer<typeof processingRetrySchema>;

export const searchCoverageSchema = z
  .object({
    current_documents: z.number().int().nonnegative(),
    indexed_documents: z.number().int().nonnegative(),
    failed_documents: z.number().int().nonnegative(),
    documents_without_complete_index: z.number().int().nonnegative(),
    current_chunks: z.number().int().nonnegative(),
    indexed_chunks: z.number().int().nonnegative(),
    unindexed_chunks: z.number().int().nonnegative(),
  })
  .passthrough();

export const searchHitSchema = z
  .object({
    chunk_id: z.string(),
    document_id: z.string(),
    parse_batch_id: z.string(),
    position: z.number().int().nonnegative(),
    text: z.string(),
    locator: z.unknown(),
    title: z.string(),
    source_id: z.string(),
    version: z.number().int().positive(),
    published_at: nullableString,
    source_updated_at: nullableString,
    collected_at: z.string(),
    completeness: z.string(),
    similarity: z.number().nullable(),
  })
  .passthrough();

export type SearchHit = z.infer<typeof searchHitSchema>;

export const searchResponseSchema = z
  .object({
    mode: z.enum(["semantic", "exact", "context"]),
    query: z.string(),
    filters: z.unknown(),
    coverage: searchCoverageSchema.nullable(),
    truncated: z.boolean(),
    hits: z.array(searchHitSchema),
  })
  .passthrough();

export type SearchResponse = z.infer<typeof searchResponseSchema>;

export const documentVersionSchema = z
  .object({
    document_id: z.string(),
    version: z.number().int().positive(),
    content_hash: z.string(),
    content_size: nullableNumber,
    content_type: nullableString,
    completeness: z.string(),
    collected_at: z.string(),
    is_current_collection_pointer: z.boolean(),
  })
  .passthrough();

export const chunkSchema = z
  .object({
    chunk_id: z.string(),
    position: z.number().int().nonnegative(),
    text: z.string(),
    locator: z.unknown(),
  })
  .passthrough();

export const originalDescriptorSchema = z
  .object({
    stored: z.boolean(),
    readable: z.boolean(),
    storage_key: nullableString,
    content_type: nullableString,
    content_size: nullableNumber,
    content_hash: nullableString,
    endpoint: nullableString,
    document_id: nullableString,
  })
  .passthrough();

export type OriginalDescriptor = z.infer<typeof originalDescriptorSchema>;

export const documentDetailSchema = z
  .object({
    document: documentSummarySchema,
    processing_stages: z.array(processingStageSchema),
    collection_state: collectionStateSchema.nullable(),
    parse_state: parseStateSchema.nullable(),
    embedding_state: embeddingStateSchema.nullable(),
    is_current_collection_pointer: z.boolean(),
    versions: pageSchema(documentVersionSchema).omit({ limit: true }).extend({
      limit: z.number().int(),
    }),
    // /api/documents/{id} returns count/items/next_cursor/limit for chunks;
    // it never sends has_more, so this is not the generic page wrapper.
    current_chunks: z
      .object({
        items: z.array(chunkSchema),
        next_cursor: nullableString,
        count: z.number().int().nonnegative(),
        limit: z.number().int(),
      })
      .passthrough(),
    original: originalDescriptorSchema,
  })
  .passthrough();

export type DocumentDetail = z.infer<typeof documentDetailSchema>;
export type DocumentChunk = z.infer<typeof chunkSchema>;
export type DocumentVersion = z.infer<typeof documentVersionSchema>;

export const factSchema = z
  .object({
    id: z.string(),
    document_id: z.string(),
    fact_type: z.string(),
    origin_method: z.string(),
    evidence_ref: z.unknown(),
    subject_name: nullableString,
    symbol: nullableString,
    metric_id: nullableString,
    statement: z.string(),
    value: z.unknown(),
    unit: nullableString,
    period_start: nullableString,
    period_end: nullableString,
    available_at: nullableString,
    attributes: z.unknown(),
    processor_version: nullableString,
    created_at: nullableString,
    // V2-03 comparison flags: the same Fact may be listed beside another value
    // or beside a different basis instead of being merged with it.
    comparison_id: nullableString.optional(),
    conflict: z.boolean().optional(),
    value_conflict: z.boolean().optional(),
    basis_mismatch: z.boolean().optional(),
    period_mismatch: z.boolean().optional(),
  })
  .passthrough();

export const factsResponseSchema = pageSchema(factSchema);

export type Fact = z.infer<typeof factSchema>;

export const reportListItemSchema = recentReportSchema.extend({
  analysis_id: z.string(),
  run_status: nullableString,
});

export type ReportListItem = z.infer<typeof reportListItemSchema>;

export const reportsResponseSchema = pageSchema(reportListItemSchema);

export const documentOriginSchema = z
  .object({
    kind: z.string(),
    key: z.string(),
    url: nullableString,
    declared_sources: z
      .array(z.object({ name: z.string(), url: z.string() }).passthrough())
      .optional(),
  })
  .passthrough();

export type DocumentOrigin = z.infer<typeof documentOriginSchema>;

export const originGroupSchema = z
  .object({
    origin_id: z.string(),
    kind: z.string(),
    key: z.string(),
    url: nullableString,
    source_ids: z.array(z.string()),
    document_count: z.number().int().positive(),
    merged: z.boolean(),
  })
  .passthrough();

export type OriginGroup = z.infer<typeof originGroupSchema>;

// Only the independence counts this page shows are parsed; the rest of the
// stored coverage stays available as-is.
export const evidenceCoverageSchema = z
  .object({
    evidence_group_count: nullableNumber.optional(),
    origin_group_count: nullableNumber.optional(),
    merged_origin_group_count: nullableNumber.optional(),
    origin_groups: z.array(originGroupSchema).optional(),
  })
  .passthrough();

export const metricComparisonFactSchema = z
  .object({
    fact_id: nullableString,
    document_id: nullableString,
    value: z.unknown(),
    unit: nullableString,
    currency: nullableString,
    period_start: nullableString,
    period_end: nullableString,
    available_at: nullableString,
    statement: nullableString,
  })
  .passthrough();

export const metricComparisonSchema = z
  .object({
    comparison_id: z.string(),
    subject_name: nullableString,
    symbol: nullableString,
    metric_id: z.string(),
    value_conflict: z.boolean(),
    basis_mismatch: z.boolean(),
    period_mismatch: z.boolean(),
    fact_count: z.number().int().nonnegative(),
    units: z.array(z.string()),
    facts: z.array(metricComparisonFactSchema),
    note: nullableString,
  })
  .passthrough();

export type MetricComparison = z.infer<typeof metricComparisonSchema>;

export const evidenceDocumentSchema = documentSummarySchema.partial().extend({
  id: z.string(),
  source_id: z.string(),
  version: z.number().int().positive(),
  title: z.string(),
  completeness: z.string(),
  collected_at: z.string(),
});

export const reportEvidenceSchema = z
  .object({
    chunk_id: z.string(),
    document_id: z.string(),
    parse_batch_id: z.string(),
    position: z.number().int().nonnegative().nullable(),
    document_version: z.number().int().positive().nullable(),
    cited_document_version: z.number().int().positive().nullable(),
    version_matches: z.boolean(),
    role: nullableString,
    reason: nullableString,
    similarity: z.number().nullable().optional(),
    query_rank: nullableNumber,
    estimated_tokens: nullableNumber,
    evidence_role: nullableString,
    kinds: z.array(z.string()).optional(),
    reasons: z.array(z.string()).optional(),
    fact_ids: z.array(z.string()).optional(),
    facts: z.array(factSchema).optional(),
    origin_id: nullableString.optional(),
    origin: documentOriginSchema.nullable().optional(),
    chunk_available: z.boolean(),
    text: nullableString,
    locator: z.unknown(),
    parse_is_current: z.boolean(),
    parse_state: parseStateSchema.nullable(),
    embedding_state: embeddingStateSchema.nullable(),
    document: evidenceDocumentSchema,
    original: originalDescriptorSchema,
    is_current_collection_pointer: z.boolean(),
    collection_state: collectionStateSchema.nullable(),
  })
  .passthrough();

export type ReportEvidence = z.infer<typeof reportEvidenceSchema>;

export const reportDetailSchema = z
  .object({
    report: z
      .object({
        report_id: z.string(),
        run_id: z.string(),
        analysis_id: z.string(),
        task_id: z.string(),
        title: z.string(),
        content: z.string(),
        created_at: z.string(),
      })
      .passthrough(),
    run: z.unknown(),
    analysis: z
      .object({
        summary: z.string(),
        findings: z.array(z.unknown()),
        open_questions: z.array(z.unknown()),
        evidence_refs: z.array(z.unknown()),
        created_at: z.string(),
      })
      .passthrough(),
    evidence_package: z
      .object({
        evidence_package_id: z.string(),
        run_id: z.string(),
        objective: z.string(),
        scope: z.unknown(),
        as_of: z.string(),
        selection_policy_version: nullableString,
        selection_budget: z.unknown(),
        queries: z.unknown(),
        coverage: z.unknown(),
        metric_comparisons: z.array(metricComparisonSchema).optional(),
        final_metric_comparisons: z.array(metricComparisonSchema).optional(),
        candidates_discovered: nullableNumber,
        candidates_screened: nullableNumber,
        selected_count: z.number().int().nonnegative(),
        unexamined_count: nullableNumber,
        retrieval_truncated: z.boolean(),
        created_at: z.string(),
      })
      .passthrough(),
    evidence: z.array(reportEvidenceSchema),
    cited_chunk_ids: z.array(z.string()),
    usage: usageSummarySchema,
    coverage_gaps: z.array(z.string()),
    latest_collection_states: z.array(collectionStateSchema),
  })
  .passthrough();

export type ReportDetail = z.infer<typeof reportDetailSchema>;

const comparisonGroupSchema = <T extends z.ZodType>(item: T) =>
  z
    .object({
      count: z.number().int().nonnegative(),
      items: z.array(item),
      truncated: z.boolean(),
    })
    .passthrough();

export const comparisonSideSchema = z
  .object({
    report_id: z.string(),
    run_id: z.string(),
    analysis_id: z.string(),
    task_id: z.string(),
    title: z.string(),
    created_at: z.string(),
    run_status: nullableString,
    as_of: nullableString,
    evidence_package_id: nullableString,
    selection_policy_version: nullableString,
    scope: z.unknown(),
    summary: nullableString,
    analysis_created_at: nullableString,
    coverage: z
      .object({
        selected_count: nullableNumber,
        evidence_group_count: nullableNumber,
        origin_group_count: nullableNumber,
        merged_origin_group_count: nullableNumber,
        fact_count: nullableNumber,
        candidates_discovered: nullableNumber,
        candidates_screened: nullableNumber,
        unexamined_count: nullableNumber,
        retrieval_truncated: z.boolean().nullable().optional(),
        research_input_count: nullableNumber,
        gaps: z.array(z.string()).optional(),
      })
      .passthrough(),
  })
  .passthrough();

export type ComparisonSide = z.infer<typeof comparisonSideSchema>;

const comparisonDocumentSchema = z
  .object({
    report_id: nullableString,
    document_id: z.string(),
    version: nullableNumber,
    content_hash: nullableString,
    source_id: nullableString,
    dedupe_key: nullableString,
    title: nullableString,
    original_url: nullableString,
    published_at: nullableString,
    collected_at: nullableString,
    origin: documentOriginSchema.nullable().optional(),
    origin_id: nullableString,
    chunk_ids: z.array(z.string()).optional(),
    fact_ids: z.array(z.string()).optional(),
    role: nullableString,
    version_matches: z.boolean().nullable().optional(),
    only_in_report_id: nullableString,
    compared_report_id: nullableString,
    earlier_as_of: nullableString,
    collected_after_as_of: z.boolean().optional(),
    published_after_as_of: z.boolean().optional(),
  })
  .passthrough();

const comparisonFactSchema = z
  .object({
    fact_id: nullableString,
    document_id: nullableString,
    subject_name: nullableString,
    symbol: nullableString,
    metric_id: nullableString,
    fact_type: nullableString,
    value: z.unknown(),
    unit: nullableString,
    currency: nullableString,
    period_start: nullableString,
    period_end: nullableString,
    available_at: nullableString,
    statement: nullableString,
    report_id: nullableString,
  })
  .passthrough();

const metricChangeSchema = z
  .object({
    subject_name: nullableString,
    symbol: nullableString,
    metric_id: nullableString,
    value_changed: z.boolean(),
    basis_changed: z.boolean(),
    period_changed: z.boolean(),
    earlier: z.array(comparisonFactSchema),
    later: z.array(comparisonFactSchema),
  })
  .passthrough();

export const reportComparisonSchema = z
  .object({
    earlier: comparisonSideSchema,
    later: comparisonSideSchema,
    ordering: z.object({ by: z.string(), swapped: z.boolean() }).passthrough(),
    warnings: z.array(
      z.object({ code: z.string(), message: z.string() }).passthrough(),
    ),
    findings: z.object({
      appeared: comparisonGroupSchema(z.unknown()),
      disappeared: comparisonGroupSchema(z.unknown()),
      persisting: comparisonGroupSchema(
        z
          .object({
            kind: nullableString,
            statement: nullableString,
            earlier_uncertainty: nullableString,
            later_uncertainty: nullableString,
            uncertainty_changed: z.boolean(),
            rationale_changed: z.boolean(),
            evidence_changed: z.boolean(),
          })
          .passthrough(),
      ),
    }),
    open_questions: z.object({
      appeared: comparisonGroupSchema(z.string()),
      disappeared: comparisonGroupSchema(z.string()),
      persisting: comparisonGroupSchema(z.string()),
    }),
    documents: z.object({
      appeared: comparisonGroupSchema(comparisonDocumentSchema),
      disappeared: comparisonGroupSchema(comparisonDocumentSchema),
      persisting: comparisonGroupSchema(
        z
          .object({
            source_id: nullableString,
            dedupe_key: nullableString,
            title: nullableString,
            earlier: comparisonDocumentSchema,
            later: comparisonDocumentSchema,
            version_changed: z.boolean(),
            content_changed: z.boolean(),
          })
          .passthrough(),
      ),
    }),
    facts: z.object({
      appeared: comparisonGroupSchema(comparisonFactSchema),
      disappeared: comparisonGroupSchema(comparisonFactSchema),
      metric_changes: comparisonGroupSchema(metricChangeSchema),
    }),
    coverage: z
      .object({
        earlier: z.unknown(),
        later: z.unknown(),
        gaps_added: comparisonGroupSchema(z.string()),
        gaps_removed: comparisonGroupSchema(z.string()),
        unexamined_unknown: z.object({
          earlier: z.boolean(),
          later: z.boolean(),
        }),
      })
      .passthrough(),
    late_material: z
      .object({
        earlier_as_of: nullableString,
        documents: comparisonGroupSchema(comparisonDocumentSchema),
        note: nullableString,
      })
      .passthrough(),
    notes: z.array(z.string()).optional(),
  })
  .passthrough();

export type ReportComparison = z.infer<typeof reportComparisonSchema>;

export const factDetailSchema = z
  .object({
    fact: factSchema,
    document: documentSummarySchema.nullable(),
    document_versions: z
      .object({
        items: z.array(
          z
            .object({
              document_id: z.string(),
              version: z.number().int().positive(),
              content_hash: z.string(),
              completeness: z.string(),
              collected_at: z.string(),
            })
            .passthrough(),
        ),
        next_cursor: nullableString,
        has_more: z.boolean(),
        limit: z.number().int(),
      })
      .passthrough(),
  })
  .passthrough();

export const healthSchema = z.object({ status: z.string() }).passthrough();

export { jsonObject };
