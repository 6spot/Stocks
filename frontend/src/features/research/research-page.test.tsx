import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  createMemoryRouter,
  MemoryRouter,
  RouterProvider,
  useLocation,
  useNavigationType,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ComparisonSide,
  ReportComparison,
  ReportListItem,
} from "@/api/schemas";
import { ReportReader, ResearchPage } from "@/features/research/research-page";

const { reportMock, reportsMock, reportComparisonMock } = vi.hoisted(() => ({
  reportMock: vi.fn(),
  reportsMock: vi.fn(),
  reportComparisonMock: vi.fn(),
}));

vi.mock("@/api/dashboard", () => ({
  dashboardApi: {
    report: reportMock,
    reports: reportsMock,
    reportComparison: reportComparisonMock,
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
    reportComparisonMock.mockReset();
  });

  it("keeps the trust line, gap alert and evidence before the original links", async () => {
    render(
      <MemoryRouter>
        <ReportReader reportId="r1" />
      </MemoryRouter>,
    );

    expect(await screen.findByText("ACTUAL SAVED EVIDENCE TEXT")).toBeVisible();

    // Trust summary: independent originals and reference count read at a glance.
    expect(screen.getByText(/独立原件 未知 · 引用 1 条/)).toBeVisible();

    // Coverage gaps render as a single warning alert listing the gap labels.
    expect(screen.getByText("资料缺口与不确定性")).toBeVisible();
    expect(screen.getByText(/引用来源只采集到部分内容/)).toBeVisible();

    const original = screen.getByRole("link", { name: /查看安全原件/ });
    const evidenceText = screen.getByText("ACTUAL SAVED EVIDENCE TEXT");
    expect(
      evidenceText.compareDocumentPosition(original) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("mounts support sections lazily and never shows collection pointers", async () => {
    render(
      <MemoryRouter>
        <ReportReader reportId="r1" />
      </MemoryRouter>,
    );

    expect(await screen.findByText("ACTUAL SAVED EVIDENCE TEXT")).toBeVisible();

    // Evidence pack is the default tab; usage and collection pointers stay hidden.
    expect(screen.getByRole("tab", { name: "证据包" })).toBeVisible();
    expect(screen.getByText("1 个引用")).toBeVisible();
    expect(screen.queryByText("本次研究用量")).not.toBeInTheDocument();
    expect(screen.queryByText("当前采集指针")).not.toBeInTheDocument();

    // The usage tab mounts its section on demand.
    await userEvent.click(screen.getByRole("tab", { name: "研究用量" }));
    expect(await screen.findByText("本次研究用量")).toBeVisible();
    expect(screen.getByText("2 次调用未提供费用")).toBeVisible();
    expect(
      screen.getByRole("link", { name: /查看 Run 步骤与模型调用/ }),
    ).toHaveAttribute("href", "/runs?run=run1");

    // Switching back unmounts the usage section again.
    await userEvent.click(screen.getByRole("tab", { name: "证据包" }));
    expect(await screen.findByText("ACTUAL SAVED EVIDENCE TEXT")).toBeVisible();
    expect(screen.queryByText("本次研究用量")).not.toBeInTheDocument();

    // Compare mode is off, so the comparison API never fires.
    expect(reportComparisonMock).not.toHaveBeenCalled();
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

    // Source independence and metric comparisons live behind the second tab.
    await screen.findByText(/独立原件 1 · 引用 2 条/);
    await userEvent.click(
      screen.getByRole("tab", { name: "来源与指标" }),
    );
    expect(await screen.findByText("来源独立性")).toBeVisible();
    expect(screen.getByText("1 个独立原件")).toBeVisible();
    expect(screen.getByText("1 个")).toBeVisible();
    expect(
      screen.getByText(/这些副本仍分别列在下方证据包中/),
    ).toBeVisible();

    expect(screen.getByText("同指标取值对比")).toBeVisible();
    expect(screen.getByText("数值冲突")).toBeVisible();
    expect(screen.getByText("2.4 percent")).toBeVisible();
    expect(screen.getByText("3.1 percent")).toBeVisible();

    // Per-row same-original labels live in the evidence tab; both copies stay open.
    await userEvent.click(screen.getByRole("tab", { name: "证据包" }));
    expect((await screen.findAllByText("同一原件（2 份）"))).toHaveLength(2);
    expect(screen.getAllByText(/页面声明的 canonical 链接/)).toHaveLength(2);
  });

  it("renders report content as markdown and strips script tags", async () => {
    reportMock.mockResolvedValue({
      ...report,
      report: {
        ...report.report,
        content:
          "## 结论\n\n- 要点一\n\n<script>window.alert('unsafe')</script>",
      },
    });
    render(
      <MemoryRouter>
        <ReportReader reportId="r1" />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "结论" }),
    ).toBeVisible();
    expect(screen.getByText("要点一")).toBeVisible();
    expect(document.querySelector("script")).not.toBeInTheDocument();
  });

  it("replaces the report body with the comparison view only in compare mode", async () => {
    reportComparisonMock.mockResolvedValue(comparisonFixture);
    render(
      <MemoryRouter>
        <ReportReader
          reportId="r1"
          compareReportId="r2"
          reports={[reportItem({ report_id: "r2", title: "Later brief" })]}
          onExitCompare={() => {}}
        />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "历史报告对照" }),
    ).toBeVisible();
    await waitFor(() =>
      expect(reportComparisonMock).toHaveBeenCalledWith(
        "r1",
        "r2",
        expect.anything(),
      ),
    );
    expect(
      screen.queryByText("The report body is shown as text."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回报告" })).toBeVisible();
  });
});

function RouteProbe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return (
    <output data-testid="route">
      {location.pathname}
      {location.search}|{navigationType}
    </output>
  );
}

function reportItem(
  overrides: Partial<ReportListItem> & { report_id: string; title: string },
): ReportListItem {
  return {
    run_id: `run-${overrides.report_id}`,
    analysis_id: `analysis-${overrides.report_id}`,
    task_id: "research",
    created_at: "2026-10-20T05:00:00Z",
    run_status: "succeeded",
    ...overrides,
  };
}

function comparisonSide(reportId: string, title: string): ComparisonSide {
  return {
    report_id: reportId,
    run_id: `run-${reportId}`,
    analysis_id: `analysis-${reportId}`,
    task_id: "research",
    title,
    created_at: "2026-10-20T05:00:00Z",
    run_status: "succeeded",
    as_of: "2026-10-20T04:00:00Z",
    evidence_package_id: "p1",
    selection_policy_version: "v1",
    scope: {},
    summary: "Saved summary.",
    analysis_created_at: "2026-10-20T05:00:00Z",
    coverage: {
      selected_count: 1,
      evidence_group_count: 1,
      origin_group_count: 1,
      merged_origin_group_count: 0,
      fact_count: 0,
      candidates_discovered: null,
      candidates_screened: null,
      unexamined_count: null,
      retrieval_truncated: false,
      research_input_count: 0,
      gaps: [],
    },
  };
}

const comparisonFixture: ReportComparison = {
  earlier: comparisonSide("r2", "Later brief"),
  later: comparisonSide("r1", "Saved Evidence Brief"),
  ordering: { by: "created_at", requested_report_id: "r1", swapped: true },
  warnings: [],
  findings: {
    appeared: { count: 0, items: [], truncated: false },
    disappeared: { count: 0, items: [], truncated: false },
    persisting: { count: 0, items: [], truncated: false },
  },
  open_questions: {
    appeared: { count: 0, items: [], truncated: false },
    disappeared: { count: 0, items: [], truncated: false },
    persisting: { count: 0, items: [], truncated: false },
  },
  documents: {
    appeared: { count: 0, items: [], truncated: false },
    disappeared: { count: 0, items: [], truncated: false },
    persisting: { count: 0, items: [], truncated: false },
  },
  facts: {
    appeared: { count: 0, items: [], truncated: false },
    disappeared: { count: 0, items: [], truncated: false },
    metric_changes: { count: 0, items: [], truncated: false },
  },
  coverage: {
    earlier: {},
    later: {},
    gaps_added: { count: 0, items: [], truncated: false },
    gaps_removed: { count: 0, items: [], truncated: false },
    unexamined_unknown: { earlier: false, later: false },
  },
  late_material: {
    earlier_as_of: "2026-10-20T04:00:00Z",
    documents: { count: 0, items: [], truncated: false },
    note: "",
  },
  notes: [],
};

function reportsPage(items: ReportListItem[]) {
  return { items, next_cursor: null, has_more: false, limit: 100 };
}

const originalMatchMedia = window.matchMedia;

function setViewport(isMobile: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn((query: string) => ({
      matches: query === "(min-width: 80rem)" ? !isMobile : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function renderResearch(initialEntry = "/research") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <RouteProbe />
      <ResearchPage />
    </MemoryRouter>,
  );
}
describe("ResearchPage compare mode", () => {
  beforeEach(() => {
    reportMock.mockReset();
    reportMock.mockResolvedValue(report);
    reportsMock.mockReset();
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r-latest", title: "Latest succeeded report" }),
        reportItem({
          report_id: "r-older",
          title: "Older succeeded report",
          created_at: "2026-10-18T05:00:00Z",
        }),
        // A newer report from a different task: the preferred compare target
        // must still be the same-task one, not simply the newest other report.
        reportItem({
          report_id: "r-other-task",
          title: "Other task report",
          task_id: "other-task",
          created_at: "2026-10-19T05:00:00Z",
        }),
      ]),
    );
    reportComparisonMock.mockReset();
    reportComparisonMock.mockResolvedValue(comparisonFixture);
  });

  afterEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: originalMatchMedia,
    });
  });

  it("opens the comparison view from the list entry and keeps the reader off", async () => {
    setViewport(false);
    renderResearch();

    await screen.findByRole("button", { name: /Latest succeeded report/ });
    await userEvent.click(
      screen.getAllByRole("button", { name: /对照/ })[0],
    );

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-latest&compare=r-older|REPLACE",
      ),
    );
    expect(
      await screen.findByRole("heading", { name: "历史报告对照" }),
    ).toBeVisible();
    expect(reportComparisonMock).toHaveBeenCalledWith(
      "r-latest",
      "r-older",
      expect.anything(),
    );
    expect(
      screen.queryByText("The report body is shown as text."),
    ).not.toBeInTheDocument();
    // Only the selected report's detail is fetched, never the other one's.
    expect(reportMock).toHaveBeenCalledTimes(1);
    expect(reportMock).toHaveBeenCalledWith("r-latest", expect.anything());
  });

  it("prefers a same-task compare target over a newer other-task report", async () => {
    setViewport(false);
    renderResearch();

    await screen.findByRole("button", { name: /Latest succeeded report/ });
    await userEvent.click(
      screen.getAllByRole("button", { name: /对照/ })[0],
    );

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "compare=r-older",
      ),
    );
    expect(screen.getByTestId("route")).not.toHaveTextContent(
      "compare=r-other-task",
    );
  });

  it("exits compare mode through the back control and restores the report body", async () => {
    setViewport(false);
    renderResearch("/research?report=r-latest&compare=r-older");

    expect(
      await screen.findByRole("heading", { name: "历史报告对照" }),
    ).toBeVisible();
    expect(reportComparisonMock).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "返回报告" }));

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-latest|REPLACE",
      ),
    );
    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "历史报告对照" }),
    ).not.toBeInTheDocument();
    // Same report detail, so no refetch and no second comparison call.
    expect(reportMock).toHaveBeenCalledTimes(1);
    expect(reportComparisonMock).toHaveBeenCalledTimes(1);
  });

  it("clears compare mode when another report is selected", async () => {
    setViewport(false);
    renderResearch("/research?report=r-latest&compare=r-older");

    await screen.findByRole("heading", { name: "历史报告对照" });
    await userEvent.click(
      screen.getByRole("button", { name: /Older succeeded report/ }),
    );

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-older|REPLACE",
      ),
    );
    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
  });
});

describe("ResearchPage initial selection", () => {
  beforeEach(() => {
    reportMock.mockReset();
    reportMock.mockResolvedValue(report);
    reportsMock.mockReset();
    // The comparison card stays in its loading state; this suite only covers
    // which report the page selects initially.
    reportComparisonMock.mockReset();
    reportComparisonMock.mockReturnValue(new Promise(() => {}));
  });

  afterEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: originalMatchMedia,
    });
  });

  it("opens the latest successful report with replace navigation on desktop", async () => {
    setViewport(false);
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({
          report_id: "r-failed",
          title: "Newest failed run",
          run_status: "failed",
        }),
        reportItem({ report_id: "r-latest", title: "Latest succeeded report" }),
        reportItem({
          report_id: "r-older",
          title: "Older succeeded report",
          created_at: "2026-10-18T05:00:00Z",
        }),
      ]),
    );

    renderResearch();

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-latest|REPLACE",
      ),
    );
    expect(reportMock).toHaveBeenCalledWith("r-latest", expect.anything());
    expect(reportMock).not.toHaveBeenCalledWith("r-failed", expect.anything());
    expect(
      screen.getByRole("button", { name: /Latest succeeded report/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: /Newest failed run/ }),
    ).toHaveAttribute("aria-pressed", "false");

    // Manual selection still works after the automatic one.
    await userEvent.click(
      screen.getByRole("button", { name: /Older succeeded report/ }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-older|REPLACE",
      ),
    );
    expect(reportMock).toHaveBeenLastCalledWith("r-older", expect.anything());
  });

  it("keeps the selection prompt on small screens and opens the latest report on click", async () => {
    setViewport(true);
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r-latest", title: "Latest succeeded report" }),
        reportItem({ report_id: "r-older", title: "Older succeeded report" }),
      ]),
    );

    renderResearch();

    await screen.findByRole("button", { name: /Latest succeeded report/ });
    expect(screen.getByText("选择一份报告")).toBeVisible();
    expect(screen.getByTestId("route")).toHaveTextContent("/research|POP");
    expect(reportMock).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "打开最新报告" }),
    );

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-latest",
      ),
    );
    expect(reportMock).toHaveBeenCalledWith("r-latest", expect.anything());
  });

  it("keeps an explicit report param even when it is missing from the list page", async () => {
    setViewport(false);
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r-latest", title: "Latest succeeded report" }),
      ]),
    );

    renderResearch("/research?report=r-history");

    expect(await screen.findByText("Saved Evidence Brief")).toBeVisible();
    expect(reportMock).toHaveBeenCalledWith("r-history", expect.anything());
    expect(reportMock).not.toHaveBeenCalledWith("r-latest", expect.anything());
    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-history|POP",
      ),
    );
    expect(
      screen.getByRole("button", { name: /Latest succeeded report/ }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("does not create a selection when the report list is empty", async () => {
    setViewport(false);
    reportsMock.mockResolvedValue(reportsPage([]));

    renderResearch();

    expect(await screen.findByText("尚无成功报告")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "打开最新报告" }),
    ).not.toBeInTheDocument();
    expect(reportMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("route")).toHaveTextContent("/research|POP");
  });

  it("does not create a selection while the report list is loading", () => {
    setViewport(false);
    reportsMock.mockReturnValue(new Promise(() => {}));

    renderResearch();

    expect(screen.getByLabelText("正在读取报告列表")).toBeVisible();
    expect(reportMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("route")).toHaveTextContent("/research|POP");
  });

  it("does not create a selection when the report list fails", async () => {
    setViewport(false);
    reportsMock.mockRejectedValue(new Error("列表读取失败"));

    renderResearch();

    expect(await screen.findByText("列表读取失败")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "打开最新报告" }),
    ).not.toBeInTheDocument();
    expect(reportMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("route")).toHaveTextContent("/research|POP");
  });

  it("keeps explicit selections across back and forward navigation", async () => {
    setViewport(false);
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r-latest", title: "Latest succeeded report" }),
        reportItem({ report_id: "r-older", title: "Older succeeded report" }),
      ]),
    );
    const router = createMemoryRouter(
      [
        {
          path: "/research",
          element: (
            <>
              <RouteProbe />
              <ResearchPage />
            </>
          ),
        },
      ],
      {
        initialEntries: [
          "/research?report=r-older",
          "/research?report=r-latest",
        ],
        initialIndex: 1,
      },
    );

    render(<RouterProvider router={router} />);

    await waitFor(() =>
      expect(reportMock).toHaveBeenCalledWith("r-latest", expect.anything()),
    );

    await act(async () => {
      await router.navigate(-1);
    });

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-older|POP",
      ),
    );
    expect(reportMock).toHaveBeenLastCalledWith("r-older", expect.anything());

    await act(async () => {
      await router.navigate(1);
    });

    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/research?report=r-latest|POP",
      ),
    );
  });
});
