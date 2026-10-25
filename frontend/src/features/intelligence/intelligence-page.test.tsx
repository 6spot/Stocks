import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import {
  documentDetailSchema,
  searchResponseSchema,
  type DocumentsResponse,
  type SearchResponse,
  type SourcesResponse,
} from "@/api/schemas";
import { IntelligencePage } from "@/features/intelligence/intelligence-page";

const { searchMock, documentsMock, sourcesMock, documentMock, documentFactsMock } =
  vi.hoisted(() => ({
    searchMock: vi.fn(),
    documentsMock: vi.fn(),
    sourcesMock: vi.fn(),
    documentMock: vi.fn(),
    documentFactsMock: vi.fn(),
  }));

vi.mock("@/api/dashboard", () => ({
  dashboardApi: {
    search: searchMock,
    documents: documentsMock,
    sources: sourcesMock,
    document: documentMock,
    documentFacts: documentFactsMock,
  },
  originalEndpoint: (id: string) => `/api/documents/${id}/original`,
  downloadEndpoint: (id: string) =>
    `/api/documents/${id}/original?download=true`,
}));

// Fixtures below mirror the production payloads field-for field, captured from
// the live deployment: search hits carry a float `similarity` and a locator
// dict of {version, segments}; facts carry evidence_ref.position as an int and
// attributes.model_ref/model_alias; document detail chunks have
// {items, next_cursor, count, limit} without has_more.
const documentId = "d4866e9c-3033-47cb-ab8c-731b4cb8eba7";
const parseBatchId = "79093c6d-02ea-4a08-b932-6fee15b24fca";

const searchPayload: SearchResponse = {
  mode: "semantic",
  query: "数据中心资本开支",
  filters: { source_id: null },
  coverage: {
    current_documents: 20,
    indexed_documents: 20,
    failed_documents: 0,
    documents_without_complete_index: 0,
    current_chunks: 60,
    indexed_chunks: 60,
    unindexed_chunks: 0,
  },
  truncated: false,
  hits: [
    {
      chunk_id: "0f0e1d2c-3b4a-4c5d-8e9f-0a1b2c3d4e5f",
      document_id: documentId,
      parse_batch_id: parseBatchId,
      position: 0,
      text: "Operators raised data-center capital expenditure guidance for the coming fiscal year.",
      locator: {
        version: 1,
        segments: [
          {
            text: "Operators raised data-center capital expenditure guidance.",
            locator: { kind: "html", path: [2, 0, 1] },
          },
        ],
      },
      title: "Hyperscaler raises data-center capex outlook",
      source_id: "sec_press_releases",
      version: 1,
      published_at: "2026-09-17T12:55:00Z",
      source_updated_at: null,
      collected_at: "2026-10-24T13:56:12.609201Z",
      completeness: "complete",
      similarity: 0.4076465125294626,
    },
    {
      chunk_id: "aa11bb22-cc33-4d44-8e55-ff6677889900",
      document_id: "a5af5841-9ffb-4474-a19f-9605ddb2d24e",
      parse_batch_id: parseBatchId,
      position: 1,
      text: "The filing lists exact metric identifiers for the reporting period.",
      locator: null,
      title: "Quarterly filing extract",
      source_id: "sec_press_releases",
      version: 1,
      published_at: null,
      source_updated_at: null,
      collected_at: "2026-10-24T14:02:00Z",
      completeness: "complete",
      similarity: null,
    },
  ],
};

const documentSummary = {
  id: documentId,
  source_id: "sec_press_releases",
  dedupe_key:
    "e2a521a4b4eaa6ecf3c2fcef830ae5407ad1d6c558b7cb328208c03fc672fd2d",
  version: 1,
  title: "Hyperscaler raises data-center capex outlook",
  original_url: "https://www.sec.gov/newsroom/example",
  published_at: "2026-09-17T12:55:00Z",
  source_updated_at: null,
  collected_at: "2026-10-24T13:56:12.609201Z",
  trust_tier: "T1",
  completeness: "complete",
  fetch_error: null,
  content_type: "text/html; charset=utf-8",
  content_size: 68749,
  content_hash:
    "cefb0f6a1ec0b47d0d3a948279beb66318f8fce6f43fb4e59dd7ee259fde9f5c",
  snapshot_key: `sources/sec_press_releases/${documentId}/v1`,
  classification: { topics: ["capital_expenditure"] },
  processing_stages: [
    { stage: "parse", status: "completed", attempts: 1, wait_reason: null, error: null },
    { stage: "facts", status: "completed", attempts: 1, wait_reason: null, error: null },
  ],
  collection_state: {
    source_id: "sec_press_releases",
    dedupe_key:
      "e2a521a4b4eaa6ecf3c2fcef830ae5407ad1d6c558b7cb328208c03fc672fd2d",
    latest_document_id: documentId,
    completeness: "complete",
    fetch_error: null,
    published_at: "2026-09-17T12:55:00Z",
    source_updated_at: null,
    last_attempted_at: "2026-10-24T13:56:12.609201Z",
  },
  parse_state: {
    status: "completed",
    processor_version: "semantic-text-v3",
    latest_batch_id: parseBatchId,
    current_batch_id: parseBatchId,
    chunk_count: 5,
    error: null,
    attempted_at: "2026-10-24T13:56:17.017718Z",
  },
  embedding_state: {
    status: "completed",
    current: true,
    parse_batch_id: parseBatchId,
    last_attempt_parse_batch_id: parseBatchId,
    model_alias: "embedding",
    model_name: "workbuddy/bge-m3",
    dimensions: 1024,
    chunk_count: 5,
    indexed_count: 5,
    unindexed_count: 0,
    error: null,
    attempted_at: "2026-10-24T16:00:36.576327Z",
  },
};

const documentsPayload: DocumentsResponse = {
  items: [documentSummary],
  next_cursor: null,
  has_more: false,
  limit: 50,
};

const sourcesPayload: SourcesResponse = {
  items: [
    {
      source_id: "sec_press_releases",
      name: "SEC Press Releases",
      connector: "rss",
      publisher: "SEC",
      trust_tier: "T1",
      enabled: true,
      configured: true,
      persisted: true,
      collection: {
        status: "complete",
        discovered: 12,
        ingested: 10,
        reused: 1,
        skipped: 1,
        failed: 0,
        error: null,
        latest_attempt_at: "2026-10-24T13:56:12Z",
        latest_document_id: documentId,
        completeness: "complete",
        last_attempt_error: null,
      },
      freshness: {
        status: "fresh",
        expected_freshness: "P1D",
        age_seconds: 3600,
        latest_disclosure_at: "2026-09-17T12:55:00Z",
        latest_collected_at: "2026-10-24T13:56:12Z",
        basis: "latest_collected_at",
      },
    },
  ],
  next_cursor: null,
  has_more: false,
  limit: 200,
  configuration_error: null,
};

// Exactly the shape /api/documents/{id} returns for current_chunks: no has_more.
const documentDetailPayload = {
  document: documentSummary,
  processing_stages: [
    { stage: "parse", status: "completed", attempts: 1, wait_reason: null, error: null },
    { stage: "classify", status: "completed", attempts: 1, wait_reason: null, error: null },
    { stage: "facts", status: "completed", attempts: 1, wait_reason: null, error: null },
    { stage: "index", status: "completed", attempts: 1, wait_reason: null, error: null },
  ],
  collection_state: documentSummary.collection_state,
  parse_state: documentSummary.parse_state,
  embedding_state: documentSummary.embedding_state,
  is_current_collection_pointer: true,
  versions: {
    items: [
      {
        document_id: documentId,
        version: 1,
        content_hash:
          "cefb0f6a1ec0b47d0d3a948279beb66318f8fce6f43fb4e59dd7ee259fde9f5c",
        content_size: 68749,
        content_type: "text/html; charset=utf-8",
        completeness: "complete",
        collected_at: "2026-10-24T13:56:12.609201Z",
        is_current_collection_pointer: true,
      },
    ],
    next_cursor: null,
    has_more: false,
    limit: 20,
  },
  current_chunks: {
    items: [
      {
        chunk_id: "0f0e1d2c-3b4a-4c5d-8e9f-0a1b2c3d4e5f",
        position: 0,
        text: "Operators raised data-center capital expenditure guidance for the coming fiscal year.",
        locator: { version: 1, segments: [] },
      },
    ],
    next_cursor: "0",
    count: 5,
    limit: 20,
  },
  original: {
    stored: true,
    readable: true,
    storage_key: `sources/sec_press_releases/${documentId}/v1`,
    content_type: "text/html; charset=utf-8",
    content_size: 68749,
    content_hash:
      "cefb0f6a1ec0b47d0d3a948279beb66318f8fce6f43fb4e59dd7ee259fde9f5c",
    endpoint: `/api/documents/${documentId}/original`,
    document_id: documentId,
  },
};

const factsPayload = {
  items: [
    {
      id: "fddf3709-8ef1-4a89-9c4f-9e0c36776177",
      document_id: documentId,
      fact_type: "guidance_update",
      origin_method: "llm_extraction",
      evidence_ref: {
        kind: "chunk",
        chunk_id: "0f0e1d2c-3b4a-4c5d-8e9f-0a1b2c3d4e5f",
        position: 1,
        parse_batch_id: parseBatchId,
      },
      subject_name: "Hyperscaler Inc.",
      symbol: null,
      metric_id: "capex_guidance",
      statement: "Data-center capital expenditure guidance was raised.",
      value: 12.5,
      unit: "USD billions",
      period_start: "2027-01-01T00:00:00Z",
      period_end: "2027-12-31T00:00:00Z",
      available_at: "2026-10-24T14:00:00Z",
      attributes: { model_ref: "fact_extraction", model_alias: "research" },
      processor_version: "fact-extraction-v3",
      created_at: "2026-10-24T14:00:00Z",
    },
  ],
  next_cursor: null,
  has_more: false,
  limit: 200,
};

function mockResolved() {
  searchMock.mockResolvedValue(searchPayload);
  documentsMock.mockResolvedValue(documentsPayload);
  sourcesMock.mockResolvedValue(sourcesPayload);
  documentMock.mockResolvedValue(documentDetailPayload);
  documentFactsMock.mockResolvedValue(factsPayload);
}

describe("IntelligencePage against real API response shapes", () => {
  it("the schemas accept the captured production payloads", () => {
    expect(searchResponseSchema.safeParse(searchPayload).success).toBe(true);
    const detail = documentDetailSchema.safeParse(documentDetailPayload);
    if (!detail.success) {
      console.error(detail.error.issues);
    }
    expect(detail.success).toBe(true);
    expect(documentDetailPayload.current_chunks).not.toHaveProperty("has_more");
  });

  it("renders semantic hits with similarity and locator without an incompatibility error", async () => {
    mockResolved();
    render(
      <MemoryRouter initialEntries={["/intelligence?q=capex"]}>
        <IntelligencePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Hyperscaler raises data-center capex outlook")).toBeVisible();
    expect(screen.getByText("相似度 0.408")).toBeVisible();
    expect(screen.getByText("sec_press_releases · v1 · 片段 1")).toBeVisible();
    expect(
      screen.queryByText("API 响应与当前控制台版本不兼容。"),
    ).not.toBeInTheDocument();
    // The hit without a similarity value renders no similarity badge.
    expect(screen.getAllByText(/相似度/)).toHaveLength(1);
  });

  it("opens a document detail from a hit and shows chunks and facts", async () => {
    mockResolved();
    render(
      <MemoryRouter initialEntries={["/intelligence?q=capex"]}>
        <IntelligencePage />
      </MemoryRouter>,
    );

    const hit = await screen.findByRole("button", {
      name: "查看 Hyperscaler raises data-center capex outlook 的 Document 详情",
    });
    await userEvent.click(hit);

    expect(documentMock).toHaveBeenCalledWith(documentId, expect.anything());
    expect(await screen.findByText("当前可读片段")).toBeVisible();
    // The chunk text is rendered both in the hit card and in the detail chunk.
    expect(
      (await screen.findAllByText(
        "Operators raised data-center capital expenditure guidance for the coming fiscal year.",
      )).length,
    ).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Data-center capital expenditure guidance was raised.")).toBeVisible();
    expect(
      screen.queryByText("API 响应与当前控制台版本不兼容。"),
    ).not.toBeInTheDocument();
  });

  it("renders the recent-documents list and opens the detail from it", async () => {
    mockResolved();
    render(
      <MemoryRouter initialEntries={["/intelligence"]}>
        <IntelligencePage />
      </MemoryRouter>,
    );

    const row = await screen.findByRole("button", {
      name: /Hyperscaler raises data-center capex outlook/,
    });
    await userEvent.click(row);

    expect(await screen.findByText("当前可读片段")).toBeVisible();
    expect(documentFactsMock).toHaveBeenCalledWith(documentId, expect.anything());
    expect(
      screen.queryByText("API 响应与当前控制台版本不兼容。"),
    ).not.toBeInTheDocument();
  });
});
