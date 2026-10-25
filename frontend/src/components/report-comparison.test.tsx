import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { ReportComparisonView } from "@/components/report-comparison";
import type {
  ComparisonSide,
  ReportComparison,
  ReportListItem,
} from "@/api/schemas";

const { comparisonMock } = vi.hoisted(() => ({
  comparisonMock: vi.fn(),
}));

vi.mock("@/api/dashboard", () => ({
  dashboardApi: {
    reportComparison: comparisonMock,
  },
  originalEndpoint: (id: string) => `/api/documents/${id}/original`,
}));

function group<T>(items: T[]) {
  return { count: items.length, items, truncated: false };
}

function side(overrides: Partial<ComparisonSide> = {}): ComparisonSide {
  return {
    report_id: "r1",
    run_id: "run1",
    analysis_id: "a1",
    task_id: "research",
    title: "Earlier brief",
    created_at: "2026-10-20T02:00:00Z",
    run_status: "succeeded",
    as_of: "2026-10-20T02:00:00Z",
    evidence_package_id: "p1",
    selection_policy_version: "v2",
    scope: { regions: ["US"] },
    summary: "Saved macro summary.",
    analysis_created_at: "2026-10-20T02:00:00Z",
    coverage: {
      selected_count: 1,
      evidence_group_count: 1,
      origin_group_count: 1,
      merged_origin_group_count: 0,
      fact_count: 1,
      candidates_discovered: null,
      candidates_screened: null,
      unexamined_count: null,
      retrieval_truncated: false,
      research_input_count: 0,
      gaps: ["partial_source_content"],
    },
    ...overrides,
  };
}

const comparison: ReportComparison = {
  earlier: side({}),
  later: side({
    report_id: "r2",
    title: "Later brief",
    created_at: "2026-10-21T02:00:00Z",
    as_of: "2026-10-21T02:00:00Z",
    coverage: {
      selected_count: 1,
      evidence_group_count: 1,
      origin_group_count: 1,
      merged_origin_group_count: 0,
      fact_count: 1,
      candidates_discovered: 3,
      candidates_screened: 3,
      unexamined_count: 0,
      retrieval_truncated: false,
      research_input_count: 0,
      gaps: [],
    },
  }),
  ordering: { by: "created_at", requested_report_id: "r2", swapped: true },
  warnings: [
    { code: "different_scope", message: "两份报告的 Scope 不同。" },
  ],
  findings: {
    appeared: group([
      {
        kind: "model_inference",
        statement: "A pause is more likely.",
        uncertainty: "结合两份资料推断。",
      },
    ]),
    disappeared: group([
      {
        kind: "source_opinion",
        statement: "Analysts expect a pause.",
        uncertainty: "媒体观点。",
      },
    ]),
    persisting: group([
      {
        kind: "fact",
        statement: "Inflation eased to 2.4%.",
        earlier_uncertainty: "单一来源，尚未复核。",
        later_uncertainty: "第二份资料给出不同数值，冲突尚未解决。",
        uncertainty_changed: true,
        rationale_changed: false,
        evidence_changed: true,
      },
    ]),
  },
  open_questions: {
    appeared: group(["Which CPI basis is comparable?"]),
    disappeared: group([]),
    persisting: group(["Were later revisions published?"]),
  },
  documents: {
    appeared: group([]),
    disappeared: group([]),
    persisting: group([
      {
        source_id: "sec",
        dedupe_key: "release-1",
        title: "CPI release",
        earlier: { document_id: "d1", version: 1, title: "CPI release" },
        later: { document_id: "d2", version: 2, title: "CPI release" },
        version_changed: true,
        content_changed: true,
      },
    ]),
  },
  facts: {
    appeared: group([]),
    disappeared: group([]),
    metric_changes: group([
      {
        subject_name: "US CPI",
        symbol: null,
        metric_id: "cpi_yoy",
        value_changed: true,
        basis_changed: false,
        period_changed: false,
        earlier: [
          {
            fact_id: "f1",
            document_id: "d1",
            value: 2.4,
            unit: "percent",
            currency: "USD",
            period_end: "2026-09-30T00:00:00Z",
          },
        ],
        later: [
          {
            fact_id: "f2",
            document_id: "d2",
            value: 2.1,
            unit: "percent",
            currency: "USD",
            period_end: "2026-09-30T00:00:00Z",
          },
        ],
      },
    ]),
  },
  coverage: {
    earlier: {},
    later: {},
    gaps_added: group([]),
    gaps_removed: group(["partial_source_content"]),
    unexamined_unknown: { earlier: true, later: false },
  },
  late_material: {
    earlier_as_of: "2026-10-20T02:00:00Z",
    documents: group([
      {
        document_id: "d2",
        version: 2,
        title: "CPI release (revised)",
        published_at: "2026-10-21T00:00:00Z",
        collected_at: "2026-10-21T02:00:00Z",
        collected_after_as_of: true,
        published_after_as_of: true,
      },
    ]),
    note: "These Documents were published or collected after the earlier report's reference time.",
  },
  notes: ["Only the stored snapshots are compared."],
};

function reports(): ReportListItem[] {
  return [
    reportItem({ report_id: "r2", title: "Later brief", created_at: "2026-10-21T02:00:00Z" }),
    reportItem({ report_id: "r1", title: "Earlier brief", created_at: "2026-10-20T02:00:00Z" }),
  ];
}

function reportItem(overrides: {
  report_id: string;
  title: string;
  created_at?: string;
}): ReportListItem {
  return {
    run_id: `run-${overrides.report_id}`,
    analysis_id: `analysis-${overrides.report_id}`,
    task_id: "research",
    created_at: overrides.created_at ?? "2026-10-20T05:00:00Z",
    run_status: "succeeded",
    ...overrides,
  };
}

describe("ReportComparisonView", () => {
  beforeEach(() => {
    comparisonMock.mockReset();
    comparisonMock.mockResolvedValue(comparison);
  });

  it("compares the report passed from the page and switches via the select", async () => {
    render(
      <ReportComparisonView
        reportId="r2"
        otherReportId="r1"
        reports={reports()}
        onChangeOther={() => {}}
      />,
    );

    // The page owns the compared report, so the view loads it immediately.
    expect(await screen.findByText("Earlier brief")).toBeVisible();
    expect(screen.getByText("Later brief")).toBeVisible();
    expect(comparisonMock).toHaveBeenCalledWith("r2", "r1", expect.anything());

    expect(screen.getByText("两份报告的 Scope 不同。")).toBeVisible();
    expect(screen.getByText("A pause is more likely.")).toBeVisible();
    expect(screen.getByText("Analysts expect a pause.")).toBeVisible();
    expect(screen.getByText(/fact · 不确定性：已变化/)).toBeVisible();
    expect(screen.getByText(/引用证据变化/)).toBeVisible();

    // Both cited versions stay openable, one per side.
    const earlier = screen.getByRole("link", { name: /打开较早原件/ });
    const later = screen.getByRole("link", { name: /打开较新原件/ });
    expect(earlier).toHaveAttribute("href", "/api/documents/d1/original");
    expect(later).toHaveAttribute("href", "/api/documents/d2/original");

    expect(screen.getByText("cpi_yoy")).toBeVisible();
    expect(screen.getByText(/2\.4 percent USD/)).toBeVisible();
    expect(screen.getByText(/2\.1 percent USD/)).toBeVisible();
    expect(screen.getByText("CPI release (revised)")).toBeVisible();
    expect(screen.getByText("partial_source_content")).toBeVisible();
  });

  it("reports the changed selection back to the page through onChangeOther", async () => {
    const onChangeOther = vi.fn();
    const user = userEvent.setup();
    render(
      <ReportComparisonView
        reportId="r2"
        otherReportId="r1"
        reports={[
          reports()[1],
          reportItem({
            report_id: "r-other",
            title: "Other brief",
            created_at: "2026-10-19T02:00:00Z",
          }),
          reports()[0],
        ]}
        onChangeOther={onChangeOther}
      />,
    );

    await screen.findByText("Earlier brief");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "对比报告" }),
      "r-other",
    );
    expect(onChangeOther).toHaveBeenCalledWith("r-other");
  });

  it("stays hidden when there is no other report to compare with", () => {
    const { container } = render(
      <ReportComparisonView
        reportId="r1"
        otherReportId="r2"
        reports={[reports()[1]]}
        onChangeOther={() => {}}
      />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(comparisonMock).not.toHaveBeenCalled();
  });
});
