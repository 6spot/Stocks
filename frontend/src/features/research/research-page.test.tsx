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
  usage: {
    call_count: 2,
    succeeded_calls: 2,
    failed_calls: 0,
    fallback_calls: 1,
    prompt_tokens: 1200,
    completion_tokens: 300,
    total_tokens: 1500,
    duration_ms: 4200,
    cost_usd: null,
    cost_known_calls: 0,
    cost_unknown_calls: 2,
    models: [
      {
        provider: "default",
        model_name: "research-model",
        calls: 2,
        total_tokens: 1500,
        cost_usd: null,
        cost_unknown_calls: 2,
      },
    ],
    models_truncated: false,
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

    // Unreported cost stays unknown and the Run stays reachable from the report.
    expect(screen.getByText("本次研究用量")).toBeVisible();
    expect(screen.getByText("2 次调用未提供费用")).toBeVisible();
    expect(
      screen.getByRole("link", { name: /查看 Run 步骤与模型调用/ }),
    ).toHaveAttribute("href", "/runs?run=run1");
  });

  it("counts confirmed reprints once and lists conflicting values as published", async () => {
    const origin = {
      kind: "canonical_url",
      key: "canonical_url:https://origin.example/story",
      url: "https://origin.example/story",
      declared_sources: [{ name: "Wire", url: "https://wire.example/feed" }],
    };
    reportMock.mockResolvedValue({
      ...report,
      evidence_package: {
        ...report.evidence_package,
        coverage: {
          evidence_group_count: 3,
          origin_group_count: 1,
          merged_origin_group_count: 1,
          origin_groups: [
            {
              origin_id: "origin-1",
              kind: "canonical_url",
              key: "canonical_url:https://origin.example/story",
              url: "https://origin.example/story",
              source_ids: ["wire", "mirror"],
              document_count: 2,
              merged: true,
            },
          ],
        },
        metric_comparisons: [
          {
            comparison_id: "comparison-1",
            subject_name: "US CPI",
            symbol: null,
            metric_id: "cpi_yoy",
            value_conflict: true,
            basis_mismatch: false,
            period_mismatch: false,
            fact_count: 2,
            units: ["percent"],
            facts: [
              {
                fact_id: "f1",
                document_id: "d1",
                value: 2.4,
                unit: "percent",
                currency: null,
                period_start: null,
                period_end: "2026-09-30T00:00:00Z",
                available_at: "2026-10-20T01:00:00Z",
                statement: "The bureau reported 2.4 percent.",
              },
              {
                fact_id: "f2",
                document_id: "d2",
                value: 3.1,
                unit: "percent",
                currency: null,
                period_start: null,
                period_end: "2026-09-30T00:00:00Z",
                available_at: "2026-10-20T01:00:00Z",
                statement: "A commentary reported 3.1 percent.",
              },
            ],
            note: "Values are listed as published.",
          },
        ],
      },
      evidence: [
        { ...report.evidence[0], origin_id: "origin-1", origin },
        {
          ...report.evidence[0],
          chunk_id: "c2",
          document_id: "d2",
          origin_id: "origin-1",
          origin,
          document: { ...report.evidence[0].document, id: "d2" },
        },
      ],
    });
    render(
      <MemoryRouter>
        <ReportReader reportId="r1" />
      </MemoryRouter>,
    );

    expect(await screen.findByText("来源独立性")).toBeVisible();
    expect(screen.getByText("1 个独立原件")).toBeVisible();
    expect(screen.getByText("1 个")).toBeVisible();
    // Both copies stay open, each labelled as the same original.
    expect(screen.getAllByText("同一原件（2 份）")).toHaveLength(2);
    expect(screen.getAllByText(/页面声明的 canonical 链接/)).toHaveLength(2);
    expect(
      screen.getByText(/这些副本仍分别列在下方证据包中/),
    ).toBeVisible();

    expect(screen.getByText("同指标取值对比")).toBeVisible();
    expect(screen.getByText("数值冲突")).toBeVisible();
    expect(screen.getByText("2.4 percent")).toBeVisible();
    expect(screen.getByText("3.1 percent")).toBeVisible();
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
