import {
  documentDetailSchema,
  documentsResponseSchema,
  factDetailSchema,
  factsResponseSchema,
  processingResponseSchema,
  processingRetrySchema,
  reportComparisonSchema,
  reportDetailSchema,
  reportsResponseSchema,
  runDetailSchema,
  runMutationSchema,
  runsResponseSchema,
  searchResponseSchema,
  sourcesResponseSchema,
  tasksResponseSchema,
} from "@/api/schemas";
import { apiRequest } from "@/api/client";

function queryString(
  values: Record<string, string | number | boolean | null | undefined>,
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const dashboardApi = {
  sources(signal?: AbortSignal) {
    return apiRequest("/api/sources?limit=200", {
      schema: sourcesResponseSchema,
      signal,
    });
  },

  tasks(signal?: AbortSignal) {
    return apiRequest("/api/tasks?limit=200", {
      schema: tasksResponseSchema,
      signal,
    });
  },

  triggerTask(taskId: string) {
    return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/runs`, {
      method: "POST",
      schema: runMutationSchema,
    });
  },

  runs(
    options: {
      taskId?: string | null;
      status?: string | null;
      cursor?: string | null;
      limit?: number;
    } = {},
    signal?: AbortSignal,
  ) {
    return apiRequest(
      `/api/runs${queryString({
        task_id: options.taskId,
        status: options.status,
        cursor: options.cursor,
        limit: options.limit ?? 50,
      })}`,
      { schema: runsResponseSchema, signal },
    );
  },

  run(runId: string, signal?: AbortSignal) {
    return apiRequest(`/api/runs/${encodeURIComponent(runId)}`, {
      schema: runDetailSchema,
      signal,
    });
  },

  retryRun(runId: string) {
    return apiRequest(`/api/runs/${encodeURIComponent(runId)}/retry`, {
      method: "POST",
      schema: runMutationSchema,
    });
  },

  continueRun(runId: string) {
    return apiRequest(`/api/runs/${encodeURIComponent(runId)}/continue`, {
      method: "POST",
      body: { resumed_from_run_id: runId },
      schema: runMutationSchema,
    });
  },

  endRun(runId: string, confirmStopped: boolean) {
    return apiRequest(`/api/runs/${encodeURIComponent(runId)}/end`, {
      method: "POST",
      body: { confirm_stopped: confirmStopped },
      schema: runMutationSchema,
    });
  },

  processing(
    options: {
      stage?: string | null;
      status?: string | null;
      cursor?: string | null;
      documentId?: string | null;
      limit?: number;
    } = {},
    signal?: AbortSignal,
  ) {
    return apiRequest(
      `/api/processing${queryString({
        stage: options.stage,
        status: options.status,
        cursor: options.cursor,
        document_id: options.documentId,
        limit: options.limit ?? 50,
      })}`,
      { schema: processingResponseSchema, signal },
    );
  },

  retryProcessing(documentId: string, stages: string[], confirmStopped = false) {
    return apiRequest("/api/processing/retry", {
      method: "POST",
      body: {
        document_id: documentId,
        stages,
        confirm_stopped: confirmStopped,
      },
      schema: processingRetrySchema,
    });
  },

  search(
    options: {
      query?: string | null;
      exact?: string | null;
      chunkId?: string | null;
      radius?: number;
      sourceId?: string | null;
      publishedFrom?: string | null;
      publishedTo?: string | null;
      collectedFrom?: string | null;
      collectedTo?: string | null;
      limit?: number;
    },
    signal?: AbortSignal,
  ) {
    return apiRequest(
      `/api/search${queryString({
        query: options.query,
        exact: options.exact,
        chunk_id: options.chunkId,
        radius: options.radius,
        source_id: options.sourceId,
        published_from: options.publishedFrom,
        published_to: options.publishedTo,
        collected_from: options.collectedFrom,
        collected_to: options.collectedTo,
        limit: options.limit ?? 30,
      })}`,
      { schema: searchResponseSchema, signal },
    );
  },

  documents(
    options: { cursor?: string | null; limit?: number } = {},
    signal?: AbortSignal,
  ) {
    return apiRequest(
      `/api/documents${queryString({
        cursor: options.cursor,
        limit: options.limit ?? 50,
      })}`,
      { schema: documentsResponseSchema, signal },
    );
  },

  document(documentId: string, signal?: AbortSignal) {
    return apiRequest(`/api/documents/${encodeURIComponent(documentId)}`, {
      schema: documentDetailSchema,
      signal,
    });
  },

  documentFacts(documentId: string, signal?: AbortSignal) {
    return apiRequest(
      `/api/documents/${encodeURIComponent(documentId)}/facts?limit=200`,
      { schema: factsResponseSchema, signal },
    );
  },

  facts(
    options: {
      sourceId?: string | null;
      documentId?: string | null;
      factType?: string | null;
      symbol?: string | null;
      metricId?: string | null;
      limit?: number;
    } = {},
    signal?: AbortSignal,
  ) {
    return apiRequest(
      `/api/facts${queryString({
        source_id: options.sourceId,
        document_id: options.documentId,
        fact_type: options.factType,
        symbol: options.symbol,
        metric_id: options.metricId,
        limit: options.limit ?? 100,
      })}`,
      { schema: factsResponseSchema, signal },
    );
  },

  fact(documentId: string, signal?: AbortSignal) {
    return apiRequest(`/api/facts/${encodeURIComponent(documentId)}`, {
      schema: factDetailSchema,
      signal,
    });
  },

  reports(limit = 100, signal?: AbortSignal) {
    return apiRequest(`/api/reports${queryString({ limit })}`, {
      schema: reportsResponseSchema,
      signal,
    });
  },

  report(reportId: string, signal?: AbortSignal) {
    return apiRequest(`/api/reports/${encodeURIComponent(reportId)}`, {
      schema: reportDetailSchema,
      signal,
    });
  },

  reportComparison(
    reportId: string,
    otherReportId: string,
    signal?: AbortSignal,
  ) {
    return apiRequest(
      `/api/reports/${encodeURIComponent(reportId)}/comparison${queryString({
        other_report_id: otherReportId,
      })}`,
      { schema: reportComparisonSchema, signal },
    );
  },
};

export function originalEndpoint(documentId: string): string {
  return `/api/documents/${encodeURIComponent(documentId)}/original`;
}

export function downloadEndpoint(documentId: string): string {
  return `${originalEndpoint(documentId)}?download=true`;
}
