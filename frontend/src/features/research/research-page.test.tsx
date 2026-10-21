import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReportReader } from "@/features/research/research-page";

const { reportMock } = vi.hoisted(() => ({
  reportMock: vi.fn(),
}));

vi.mock("@/api/dashboard", () => ({
  dashboardApi: {
    report: reportMock,
  },
  originalEndpoint: (id: string) => `/api/documents/${id}/original`,
  downloadEndpoint: (id: string) => `/api/documents/${id}/original?download=true`,
}));

const report = {
  report: {
    report_id: "r1",
    run_id: "run1",
    analysis_id: "a1",
    task_id: "research",
    title: "Saved Evidence Brief",
    content: "The report body is shown as text.",
    created_at: "2026-10-20T05:00:00Z",
  },
  run: { status: "succeeded" },
  analysis: {
    summary: "",
    findings: [],
    open_questions: [],
    evidence_refs: [],
    created_at: "2026-10-20T05:00:00Z",
  },
  evidence_package: {
    evidence_package_id: "p1",
    run_id: "run1",
    objective: "test",
    scope: {},
    as_of: "2026-10-20T04:00:00Z",
    selection_policy_version: "v1",
    selection_budget: {},
    queries: {},
    coverage: {},
    candidates_discovered: 1,
    candidates_screened: 1,
    selected_count: 1,
    unexamined_count: 0,
    retrieval_truncated: false,
    created_at: "2026-10-20T04:30:00Z",
  },
  evidence: [
    {
      chunk_id: "c1",
      document_id: "d1",
      parse_batch_id: "b1",
      position: 0,
      document_version: 1,
      cited_document_version: 1,
      version_matches: true,
      role: "direct",
      reason: "quoted",
      similarity: null,
      query_rank: 1,
      estimated_tokens: 20,
      chunk_available: true,
      text: "ACTUAL SAVED EVIDENCE TEXT",
      locator: { kind: "text", char_start: 0 },
      parse_is_current: true,
      parse_state: {
        status: "completed",
        chunk_count: 1,
      },
      embedding_state: {
        status: "completed",
        current: true,
        chunk_count: 1,
        indexed_count: 1,
        unindexed_count: 0,
      },
      document: {
        id: "d1",
        source_id: "sec",
        version: 1,
        title: "Original filing",
        completeness: "complete",
        collected_at: "2026-10-20T01:00:00Z",
        published_at: "2026-10-19T00:00:00Z",
      },
      original: {
        stored: true,
        readable: true,
        endpoint: "/api/documents/d1/original",
        document_id: "d1",
      },
      is_current_collection_pointer: false,
      collection_state: null,
    },
  ],
  cited_chunk_ids: ["c1"],
  coverage_gaps: ["partial_source_content"],
  latest_collection_states: [
    {
      source_id: "sec",
      dedupe_key: "filing",
      latest_document_id: "d2",
      completeness: "complete",
      published_at: "2026-10-21T00:00:00Z",
      source_updated_at: null,
      last_attempted_at: "2026-10-21T01:00:00Z",
    },
  ],
};

describe("ReportReader", () => {
  beforeEach(() => {
    reportMock.mockReset();
    reportMock.mockResolvedValue(report);
  });

  it("shows the saved chunk and version before exposing the original", async () => {
    render(
      <MemoryRouter>
        <ReportReader reportId="r1" />
      </MemoryRouter>,
    );

    expect(await screen.findByText("ACTUAL SAVED EVIDENCE TEXT")).toBeVisible();
    expect(screen.getByText(/引用版本/)).toBeVisible();
    expect(screen.getByText("当前采集指针")).toBeVisible();

    const original = screen.getByRole("link", { name: /查看安全原件/ });
    const evidenceText = screen.getByText("ACTUAL SAVED EVIDENCE TEXT");
    expect(
      evidenceText.compareDocumentPosition(original) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders report HTML-looking content as plain text", async () => {
    reportMock.mockResolvedValue({
      ...report,
      report: {
        ...report.report,
        content: "<script>window.alert('unsafe')</script>",
      },
    });
    render(
      <MemoryRouter>
        <ReportReader reportId="r1" />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("<script>window.alert('unsafe')</script>"),
    ).toBeVisible();
    expect(document.querySelector("script")).not.toBeInTheDocument();
  });
});
