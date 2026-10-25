import { render, screen } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import {
  sourcesResponseSchema,
  type SourcesResponse,
} from "@/api/schemas";
import { SourcesPage } from "@/features/sources/sources-page";

const { sourcesMock } = vi.hoisted(() => ({
  sourcesMock: vi.fn(),
}));

vi.mock("@/api/dashboard", () => ({
  dashboardApi: {
    sources: sourcesMock,
  },
}));

function renderPage() {
  render(
    <BrowserRouter>
      <SourcesPage />
    </BrowserRouter>,
  );
}

// Fixtures mirror the /api/sources payload field-for-field: lifecycle flags
// (enabled/configured/persisted), collection counters plus
// processing_backlog, and freshness with disclosure/collected timestamps.
const healthySource = {
  source_id: "fed_press_rss",
  name: "Federal Reserve RSS",
  connector: "rss",
  publisher: "Federal Reserve Board",
  trust_tier: "T1",
  enabled: true,
  configured: true,
  persisted: true,
  collection: {
    status: "complete",
    discovered: 8,
    ingested: 8,
    reused: 0,
    skipped: 0,
    failed: 0,
    error: null,
    latest_attempt_at: "2026-10-24T13:56:12Z",
    latest_document_id: null,
    completeness: "complete",
    last_attempt_error: null,
    processing_backlog: { pending: 0, failed: 0 },
  },
  freshness: {
    status: "fresh",
    expected_freshness: "P1D",
    age_seconds: 3600,
    latest_disclosure_at: "2026-10-24T12:00:00Z",
    latest_collected_at: "2026-10-24T13:56:12Z",
    basis: "latest_collected_at",
  },
};

const disabledSource = {
  source_id: "bls_cpi_http",
  name: "BLS CPI HTTP",
  connector: "http_api",
  publisher: "U.S. Bureau of Labor Statistics",
  trust_tier: "T2",
  enabled: false,
  configured: true,
  persisted: true,
  collection: {
    status: "source_failure",
    discovered: 4,
    ingested: 3,
    reused: 0,
    skipped: 0,
    failed: 1,
    error: null,
    latest_attempt_at: "2026-10-23T09:30:00Z",
    latest_document_id: null,
    completeness: "partial",
    last_attempt_error: {
      code: "upstream_error",
      message: "upstream returned HTTP 503",
    },
    processing_backlog: { pending: 3, failed: 1 },
  },
  freshness: {
    status: "stale",
    expected_freshness: "P1M",
    age_seconds: 900000,
    latest_disclosure_at: "2026-09-12T12:30:00Z",
    latest_collected_at: "2026-09-12T14:00:00Z",
    basis: "latest_disclosure_at",
  },
};

const sourcesPayload: SourcesResponse = {
  items: [healthySource, disabledSource],
  next_cursor: null,
  has_more: false,
  limit: 200,
  configuration_error: null,
};

const emptyPayload: SourcesResponse = {
  items: [],
  next_cursor: null,
  has_more: false,
  limit: 200,
  configuration_error: null,
};

const configuredErrorPayload: SourcesResponse = {
  items: [healthySource],
  next_cursor: null,
  has_more: false,
  limit: 200,
  configuration_error: {
    code: "sources_yaml_invalid",
    message: "sources.yaml 第 12 行缺少 connector 字段",
  },
};

describe("SourcesPage against real API response shapes", () => {
  it("the schema accepts the captured production payload", () => {
    expect(sourcesResponseSchema.safeParse(sourcesPayload).success).toBe(true);
    expect(
      sourcesResponseSchema.safeParse(configuredErrorPayload).success,
    ).toBe(true);
  });

  it("renders all sources including lifecycle badges, errors and backlog", async () => {
    sourcesMock.mockResolvedValue(sourcesPayload);
    renderPage();

    expect(
      await screen.findByText("Federal Reserve RSS"),
    ).toBeVisible();
    expect(screen.getByText("BLS CPI HTTP")).toBeVisible();
    // Secondary text under the name keeps the source_id visible.
    expect(screen.getByText("fed_press_rss")).toBeVisible();
    expect(screen.getByText("bls_cpi_http")).toBeVisible();
    expect(screen.getByText("T1 · 官方原始")).toBeVisible();
    expect(screen.getByText("T2 · 原始发布者")).toBeVisible();

    expect(screen.getByText("已启用")).toBeVisible();
    expect(screen.getByText("已禁用")).toBeVisible();

    // last_attempt_error surfaces as a readable row-level summary.
    expect(
      screen.getByText("[upstream_error] upstream returned HTTP 503"),
    ).toBeVisible();

    // Backlog counters stay visible only when non-zero.
    expect(screen.getByText("待处理 3 · 失败 1")).toBeVisible();
    expect(screen.getByText("无积压")).toBeVisible();

    expect(screen.getByText("新鲜")).toBeVisible();
    expect(screen.getByText("已过期")).toBeVisible();
    // 最近尝试时间经 formatDateTime 渲染；格式化依赖本地时区，不断言具体文本。
    expect(screen.getAllByText(/2026年10月|Oct 24|2026\/10\/24/).length)
      .toBeGreaterThan(0);
  });

  it("shows a prominent alert when sources.yaml failed to load", async () => {
    sourcesMock.mockResolvedValue(configuredErrorPayload);
    renderPage();

    expect(
      await screen.findByText("sources.yaml 配置未能读取"),
    ).toBeVisible();
    expect(
      screen.getByText("sources.yaml 第 12 行缺少 connector 字段"),
    ).toBeVisible();
    // The page still lists the sources it managed to load.
    expect(screen.getByText("Federal Reserve RSS")).toBeVisible();
  });

  it("renders the empty state when no source is configured", async () => {
    sourcesMock.mockResolvedValue(emptyPayload);
    renderPage();

    expect(
      await screen.findByText("尚未接入任何采集源"),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("notes truncation when the response has more pages", async () => {
    sourcesMock.mockResolvedValue({
      ...sourcesPayload,
      has_more: true,
    });
    renderPage();

    expect(
      await screen.findByText(/当前仅显示前 2 个/),
    ).toBeVisible();
  });

  it("renders the error panel when the request fails", async () => {
    sourcesMock.mockRejectedValue(new Error("采集源请求失败"));
    renderPage();

    expect(await screen.findByText("采集源请求失败")).toBeVisible();
    expect(screen.getByRole("button", { name: "重试" })).toBeVisible();
  });
});
