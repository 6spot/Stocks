import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Overview } from "@/api/schemas";
import { OverviewPage } from "@/features/overview/overview-page";

const { overviewMock } = vi.hoisted(() => ({
  overviewMock: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  getOverview: overviewMock,
}));

function library(
  overrides: Partial<Overview["library"]> = {},
): Overview["library"] {
  return {
    documents: 20,
    chunks: 60,
    facts: 71,
    reports: 2,
    sources: 2,
    earliest_collected_at: "2026-10-04T02:00:00Z",
    latest_collected_at: "2026-10-24T09:30:00Z",
    documents_this_week: 6,
    facts_this_week: 13,
    by_source: [
      {
        source_id: "sec_press_releases",
        name: "SEC Press Releases",
        documents: 14,
        facts: 51,
        last_success_at: "2026-10-24T09:30:00Z",
        freshness: "fresh",
        expected_freshness: "15m",
      },
      {
        source_id: "fed_press_rss",
        name: "Federal Reserve RSS",
        documents: 6,
        facts: 20,
        last_success_at: "2026-10-20T01:00:00Z",
        freshness: "stale",
        expected_freshness: "1d",
      },
    ],
    daily: [
      { date: "2026-10-11", documents: 0, facts: 0 },
      { date: "2026-10-12", documents: 2, facts: 4 },
      { date: "2026-10-13", documents: 0, facts: 0 },
      { date: "2026-10-14", documents: 1, facts: 3 },
      { date: "2026-10-15", documents: 0, facts: 0 },
      { date: "2026-10-16", documents: 0, facts: 0 },
      { date: "2026-10-17", documents: 3, facts: 6 },
      { date: "2026-10-18", documents: 0, facts: 0 },
      { date: "2026-10-19", documents: 0, facts: 0 },
      { date: "2026-10-20", documents: 4, facts: 9 },
      { date: "2026-10-21", documents: 0, facts: 0 },
      { date: "2026-10-22", documents: 0, facts: 0 },
      { date: "2026-10-23", documents: 0, facts: 0 },
      { date: "2026-10-24", documents: 10, facts: 49 },
    ],
    ...overrides,
  };
}

function overview(overrides: Partial<Overview> = {}): Overview {
  return {
    library: library(),
    attention: [],
    collection: {
      configured_sources: 2,
      partial_sources: [],
      source_errors: null,
    },
    processing: { by_status: {}, by_stage: {} },
    processing_state: {
      status: "clear",
      pending: 0,
      running: 0,
      failed: 0,
    },
    runs: { active_count: 0, failed_count: 0, recent: [] },
    reports: [
      {
        report_id: "r1",
        run_id: "run1",
        task_id: "research_task",
        title: "Saved macro brief",
        created_at: "2026-10-24T10:00:00Z",
        origin_group_count: 3,
        gap_count: 2,
      },
    ],
    research: {
      latest: null,
      usage: {
        call_count: 0,
        succeeded_calls: 0,
        failed_calls: 0,
        fallback_calls: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        duration_ms: 0,
        cost_usd: null,
        cost_known_calls: 0,
        cost_unknown_calls: 0,
        run_count: 0,
      },
    },
    recent_documents: [
      {
        id: "d1",
        source_id: "sec_press_releases",
        dedupe_key: "release-1",
        version: 1,
        title: "Quarterly update",
        original_url: null,
        published_at: "2026-10-24T08:00:00Z",
        source_updated_at: null,
        collected_at: "2026-10-24T09:30:00Z",
        trust_tier: "T1",
        completeness: "complete",
        fetch_error: null,
        content_type: "text/html",
        content_size: 100,
        content_hash: "a".repeat(64),
        snapshot_key: "sources/sec/1",
        classification: null,
        processing_stages: [
          {
            stage: "parse",
            status: "completed",
            attempts: 1,
            wait_reason: null,
            skip_reason: null,
            error: null,
          },
          {
            stage: "classify",
            status: "completed",
            attempts: 1,
            wait_reason: null,
            skip_reason: null,
            error: null,
          },
          {
            stage: "facts",
            status: "failed",
            attempts: 1,
            wait_reason: null,
            skip_reason: null,
            error: { code: "FACTS_FAILED" },
          },
          {
            stage: "index",
            status: "pending",
            attempts: 0,
            wait_reason: null,
            skip_reason: null,
            error: null,
          },
        ],
      },
    ],
    task_count: 2,
    configuration_errors: { sources: null, tasks: null },
    ...overrides,
  };
}

function renderPage() {
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
}

describe("OverviewPage", () => {
  beforeEach(() => {
    overviewMock.mockReset();
  });

  it("shows the library stat strip with weekly additions and span", async () => {
    overviewMock.mockResolvedValue(overview());
    renderPage();

    const strip = await screen.findByRole("region", { name: "情报存量" });
    const terms = within(strip).getAllByRole("term");
    expect(terms.map((term) => term.textContent)).toEqual([
      "文档",
      "Fact",
      "报告",
      "来源",
    ]);
    const values = within(strip).getAllByRole("definition");
    expect(values.map((value) => value.textContent)).toEqual([
      "20",
      "71",
      "2",
      "2",
    ]);
    expect(
      within(strip).getByText(/本周新增 文档 6 · Fact 13/),
    ).toBeVisible();
    expect(
      within(strip).getByText(/时间跨度 2026-10-04 ~ 2026-10-24/),
    ).toBeVisible();
  });

  it("renders one source table row per contributing source with freshness", async () => {
    overviewMock.mockResolvedValue(overview());
    renderPage();

    const row = (await screen.findByText("SEC Press Releases")).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("14")).toBeVisible();
    expect(within(row as HTMLElement).getByText("51")).toBeVisible();
    expect(within(row as HTMLElement).getByText("新鲜")).toBeVisible();

    const stale = screen.getByText("Federal Reserve RSS").closest("tr");
    expect(within(stale as HTMLElement).getByText("已过期")).toBeVisible();
  });

  it("lists attention items with a working link and no 无阻塞 line", async () => {
    overviewMock.mockResolvedValue(
      overview({
        attention: [
          {
            kind: "source_failure",
            severity: "warning",
            message: "Federal Reserve RSS 最近一次采集未完成",
            link: "/sources",
            source_id: "fed_press_rss",
            task_id: null,
          },
          {
            kind: "task_failed",
            severity: "error",
            message: "任务 research_task 最近一次运行失败",
            link: "/runs?status=failed",
            source_id: null,
            task_id: "research_task",
          },
        ],
      }),
    );
    renderPage();

    expect(
      await screen.findByText("Federal Reserve RSS 最近一次采集未完成"),
    ).toBeVisible();
    const failed = screen.getByText("任务 research_task 最近一次运行失败");
    expect(failed).toBeVisible();
    const link = screen
      .getAllByRole("link")
      .find((element) => element.getAttribute("href") === "/runs?status=failed");
    expect(link).toBeDefined();
    expect(screen.queryByText("无阻塞")).not.toBeInTheDocument();
  });

  it("shows 无阻塞 when attention is empty", async () => {
    overviewMock.mockResolvedValue(overview({ attention: [] }));
    renderPage();

    expect(await screen.findByText("无阻塞")).toBeVisible();
    expect(screen.queryByText(/最近一次采集未完成/)).not.toBeInTheDocument();
  });

  it("links each report row to its detail route", async () => {
    overviewMock.mockResolvedValue(overview());
    renderPage();

    const link = await screen.findByRole("link", { name: "Saved macro brief" });
    expect(link.getAttribute("href")).toBe("/research/r1");
    const row = link.closest("tr");
    expect(within(row as HTMLElement).getByText("3")).toBeVisible();
    expect(within(row as HTMLElement).getByText("2")).toBeVisible();
  });

  it("links recent documents into the intelligence detail", async () => {
    overviewMock.mockResolvedValue(overview());
    renderPage();

    const link = await screen.findByRole("link", { name: "Quarterly update" });
    expect(link.getAttribute("href")).toBe("/intelligence?document=d1");
    const row = link.closest("tr");
    expect(within(row as HTMLElement).getByText("失败")).toBeVisible();
  });

  it("renders the 14-day inflow chart with a legend instead of an empty state", async () => {
    overviewMock.mockResolvedValue(overview());
    renderPage();

    const chart = await screen.findByRole("img", {
      name: "最近 14 天文档与 Fact 新增柱状图",
    });
    expect(chart).toBeVisible();
    // The legend labels both series so the two bars are never ambiguous.
    expect(within(chart).getByText("文档")).toBeVisible();
    expect(within(chart).getByText("Fact")).toBeVisible();
    expect(screen.queryByText("最近 14 天没有新增数据")).not.toBeInTheDocument();
  });

  it("shows an empty state when the last 14 days hold no data", async () => {
    overviewMock.mockResolvedValue(
      overview({
        library: library({
          daily: library().daily.map((item) => ({
            ...item,
            documents: 0,
            facts: 0,
          })),
        }),
      }),
    );
    renderPage();

    expect(
      await screen.findByText("最近 14 天没有新增数据"),
    ).toBeVisible();
    expect(
      screen.queryByRole("img", {
        name: "最近 14 天文档与 Fact 新增柱状图",
      }),
    ).not.toBeInTheDocument();
  });
});
