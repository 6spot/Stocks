import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ComparisonSide,
  ReportComparison,
  ReportListItem,
} from "@/api/schemas";
import { useSessionStore } from "@/auth/session-store";
import { AppRouter } from "@/app/router";

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
  downloadEndpoint: (id: string) =>
    `/api/documents/${id}/original?download=true`,
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

function reportsPage(items: ReportListItem[]) {
  return { items, next_cursor: null, has_more: false, limit: 100 };
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

function renderAppRouter(initialEntry: string) {
  useSessionStore.setState({ status: "authenticated" });
  const router = createMemoryRouter(
    [
      {
        path: "*",
        element: <AppRouter />,
      },
    ],
    { initialEntries: [initialEntry] },
  );
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  useSessionStore.setState({ status: "authenticated" });
  reportMock.mockReset();
  reportMock.mockResolvedValue(report);
  reportsMock.mockReset();
  reportComparisonMock.mockReset();
});

afterEach(() => {
  useSessionStore.setState({ status: "idle", session: null, error: null });
});

describe("ResearchPage list", () => {
  it("renders the report table and opens the detail page on row click", async () => {
    const user = userEvent.setup();
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({
          report_id: "r-latest",
          title: "Latest succeeded report",
          created_at: "2026-10-20T05:00:00Z",
        }),
        reportItem({
          report_id: "r-older",
          title: "Older succeeded report",
          created_at: "2026-10-18T05:00:00Z",
        }),
        reportItem({
          report_id: "r-failed",
          title: "Newest failed run",
          run_status: "failed",
        }),
      ]),
    );

    renderAppRouter("/research");

    const table = await screen.findByRole("table", { name: "报告列表" });
    expect(within(table).getByText("Latest succeeded report")).toBeVisible();
    expect(within(table).getByText("Older succeeded report")).toBeVisible();
    expect(within(table).getByText("Newest failed run")).toBeVisible();
    expect(within(table).getAllByRole("row")).toHaveLength(4); // header + 3

    await user.click(
      within(table).getByRole("button", {
        name: "打开报告 Older succeeded report",
      }),
    );

    await waitFor(() =>
      expect(reportMock).toHaveBeenCalledWith(
        "r-older",
        expect.anything(),
      ),
    );
    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
  });

  it("navigates when clicking anywhere in the row", async () => {
    const user = userEvent.setup();
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r-latest", title: "Latest succeeded report" }),
      ]),
    );

    renderAppRouter("/research");

    const table = await screen.findByRole("table", { name: "报告列表" });
    await user.click(
      within(table).getByText("research"),
    );

    await waitFor(() =>
      expect(reportMock).toHaveBeenCalledWith(
        "r-latest",
        expect.anything(),
      ),
    );
    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
  });

  it("keeps loading, empty and error states on the list page", async () => {
    reportsMock.mockReturnValue(new Promise(() => {}));
    const { unmount } = renderAppRouter("/research");
    expect(screen.getByLabelText("正在读取报告列表")).toBeVisible();
    expect(reportMock).not.toHaveBeenCalled();
    unmount();

    reportsMock.mockResolvedValue(reportsPage([]));
    renderAppRouter("/research");
    expect(await screen.findByText("尚无成功报告")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    reportsMock.mockRejectedValue(new Error("列表读取失败"));
    renderAppRouter("/research");
    expect(await screen.findByText("列表读取失败")).toBeVisible();
  });
});

describe("ResearchDetailPage", () => {
  it("shows the single trust line and keeps the body as the first content", async () => {
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r1", title: "Saved Evidence Brief" }),
      ]),
    );
    renderAppRouter("/research/r1");

    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
    expect(reportMock).toHaveBeenCalledWith("r1", expect.anything());

    expect(screen.getByText(/独立原件 未知 · 引用 1 条/)).toBeVisible();
    expect(screen.getAllByText(/独立原件/)).toHaveLength(1);

    // 单一内联警告行替代原来两个大 Alert 块。
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        /资料缺口：引用来源只采集到部分内容，不代表资料在现实中不存在/,
      ),
    ).toBeVisible();

    // 返回列表回到列表页。
    await userEvent.click(screen.getByRole("button", { name: "返回列表" }));
    expect(await screen.findByRole("table", { name: "报告列表" })).toBeVisible();
  });

  it("renders the retrieval-truncated warning inline together with gaps", async () => {
    reportsMock.mockResolvedValue(reportsPage([]));
    reportMock.mockResolvedValue({
      ...report,
      evidence_package: {
        ...report.evidence_package,
        retrieval_truncated: true,
      },
      coverage_gaps: ["chunk_unavailable", "partial_source_content"],
    });
    renderAppRouter("/research/r1");

    expect(
      await screen.findByText(
        /证据检索已截断，报告未读取所有候选资料；资料缺口：报告引用的 Chunk 无法读取、引用来源只采集到部分内容，不代表资料在现实中不存在/,
      ),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders markdown headings and strips script tags", async () => {
    reportsMock.mockResolvedValue(reportsPage([]));
    reportMock.mockResolvedValue({
      ...report,
      report: {
        ...report.report,
        content:
          "## 结论\n\n- 要点一\n\n<script>window.alert('unsafe')</script>",
      },
    });
    renderAppRouter("/research/r1");

    expect(
      await screen.findByRole("heading", { name: "结论" }),
    ).toBeVisible();
    expect(screen.getByText("要点一")).toBeVisible();
    expect(document.querySelector("script")).not.toBeInTheDocument();
  });

  it("mounts support sections lazily and keeps the original links after evidence", async () => {
    reportsMock.mockResolvedValue(reportsPage([]));
    renderAppRouter("/research/r1");

    expect(await screen.findByText("ACTUAL SAVED EVIDENCE TEXT")).toBeVisible();

    // Evidence pack is the default tab; usage stays hidden until requested.
    expect(screen.getByRole("tab", { name: "证据包" })).toBeVisible();
    expect(screen.getByText("1 个引用")).toBeVisible();
    expect(screen.queryByText("本次研究用量")).not.toBeInTheDocument();
    expect(screen.queryByText("当前采集指针")).not.toBeInTheDocument();

    const original = screen.getByRole("link", { name: /查看安全原件/ });
    const evidenceText = screen.getByText("ACTUAL SAVED EVIDENCE TEXT");
    expect(
      evidenceText.compareDocumentPosition(original) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await userEvent.click(screen.getByRole("tab", { name: "研究用量" }));
    expect(await screen.findByText("本次研究用量")).toBeVisible();
    expect(screen.getByText("2 次调用未提供费用")).toBeVisible();
    expect(
      screen.getByRole("link", { name: /查看 Run 步骤与模型调用/ }),
    ).toHaveAttribute("href", "/runs?run=run1");

    await userEvent.click(screen.getByRole("tab", { name: "证据包" }));
    expect(await screen.findByText("ACTUAL SAVED EVIDENCE TEXT")).toBeVisible();
    expect(screen.queryByText("本次研究用量")).not.toBeInTheDocument();
    expect(reportComparisonMock).not.toHaveBeenCalled();
  });

  it("shows the source independence tab content", async () => {
    reportsMock.mockResolvedValue(reportsPage([]));
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
    renderAppRouter("/research/r1");

    await screen.findByText(/独立原件 1 · 引用 2 条/);
    await userEvent.click(screen.getByRole("tab", { name: "来源与指标" }));
    expect(await screen.findByText("来源独立性")).toBeVisible();
    expect(screen.getByText("1 个独立原件")).toBeVisible();
    expect(
      screen.getByText(/本包共有 3 个资料组、2 个引用，其中 1 个独立原件/),
    ).toBeVisible();
    expect(
      screen.getByText(/这些副本仍分别列在下方证据包中/),
    ).toBeVisible();

    expect(screen.getByText("同指标取值对比")).toBeVisible();
    expect(screen.getByText("数值冲突")).toBeVisible();
    expect(screen.getByText("2.4 percent")).toBeVisible();
    expect(screen.getByText("3.1 percent")).toBeVisible();

    await userEvent.click(screen.getByRole("tab", { name: "证据包" }));
    expect(await screen.findAllByText("同一原件（2 份）")).toHaveLength(2);
    expect(screen.getAllByText(/页面声明的 canonical 链接/)).toHaveLength(2);
  });

  it("opens compare mode and returns to the report body", async () => {
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r1", title: "Saved Evidence Brief" }),
        reportItem({
          report_id: "r-older",
          title: "Older succeeded report",
          created_at: "2026-10-18T05:00:00Z",
        }),
        // Newer but from a different task: same-task target must win.
        reportItem({
          report_id: "r-other-task",
          title: "Other task report",
          task_id: "other-task",
          created_at: "2026-10-19T05:00:00Z",
        }),
      ]),
    );
    reportComparisonMock.mockResolvedValue(comparisonFixture);
    renderAppRouter("/research/r1");

    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "对照其他报告" }),
    );

    expect(
      await screen.findByRole("heading", { name: "历史报告对照" }),
    ).toBeVisible();
    expect(reportComparisonMock).toHaveBeenCalledWith(
      "r1",
      "r-older",
      expect.anything(),
    );
    expect(
      screen.queryByText("The report body is shown as text."),
    ).not.toBeInTheDocument();
    // Only the open report's detail is fetched; the compared side comes from
    // the comparison API. The preferred target is the same-task report, not
    // the newer other-task one.
    expect(reportMock).toHaveBeenCalledTimes(1);
    expect(reportMock).toHaveBeenCalledWith("r1", expect.anything());

    // 返回报告退出对照模式并恢复正文，不重新拉取详情或对照。
    await userEvent.click(screen.getByRole("button", { name: "返回报告" }));
    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "历史报告对照" }),
    ).not.toBeInTheDocument();
    expect(reportMock).toHaveBeenCalledTimes(1);
    expect(reportComparisonMock).toHaveBeenCalledTimes(1);
  });

  it("renders compare mode directly from the ?compare= deep link", async () => {
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r1", title: "Saved Evidence Brief" }),
        reportItem({ report_id: "r2", title: "Later brief" }),
      ]),
    );
    reportComparisonMock.mockResolvedValue(comparisonFixture);
    renderAppRouter("/research/r1?compare=r2");

    expect(
      await screen.findByRole("heading", { name: "历史报告对照" }),
    ).toBeVisible();
    expect(reportComparisonMock).toHaveBeenCalledWith(
      "r1",
      "r2",
      expect.anything(),
    );
    expect(
      screen.queryByText("The report body is shown as text."),
    ).not.toBeInTheDocument();
  });

  it("navigates detail-to-detail when selecting a compared report", async () => {
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r1", title: "Saved Evidence Brief" }),
        reportItem({ report_id: "r2", title: "Later brief" }),
      ]),
    );
    reportComparisonMock.mockResolvedValue(comparisonFixture);
    renderAppRouter("/research/r1?compare=r2");

    await screen.findByRole("heading", { name: "历史报告对照" });
    // The earlier panel is r2; opening it navigates detail-to-detail and
    // drops the compare param (the new report has no compare target set).
    // 侧栏按钮在对照数据加载后才出现，不能用标题渲染时机代替。
    await userEvent.click(
      (await screen.findAllByRole("button", { name: /打开这份报告/ }))[0],
    );

    await waitFor(() =>
      expect(reportMock).toHaveBeenCalledWith("r2", expect.anything()),
    );
    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
  });

  it("shows an error with retry when the report cannot be read", async () => {
    reportsMock.mockResolvedValue(reportsPage([]));
    reportMock.mockRejectedValue(new Error("报告读取失败"));
    renderAppRouter("/research/r1");

    expect(await screen.findByText("报告读取失败")).toBeVisible();
    expect(screen.getByRole("button", { name: /重试/ })).toBeVisible();
  });
});

describe("legacy ?report= redirect", () => {
  it("redirects /research?report=<id> to the detail route with replace", async () => {
    reportsMock.mockResolvedValue(reportsPage([]));
    const router = createMemoryRouter(
      [
        {
          path: "*",
          element: <AppRouter />,
        },
      ],
      { initialEntries: ["/research?report=r1"] },
    );
    render(<RouterProvider router={router} />);

    expect(
      await screen.findByText("The report body is shown as text."),
    ).toBeVisible();
    expect(reportMock).toHaveBeenCalledWith("r1", expect.anything());
    expect(router.state.location.pathname).toBe("/research/r1");
    expect(router.state.location.search).toBe("");
  });

  it("keeps the list page when no report param is present", async () => {
    reportsMock.mockResolvedValue(
      reportsPage([
        reportItem({ report_id: "r1", title: "Saved Evidence Brief" }),
      ]),
    );
    renderAppRouter("/research");

    expect(
      await screen.findByRole("table", { name: "报告列表" }),
    ).toBeVisible();
    expect(reportMock).not.toHaveBeenCalled();
  });
});
