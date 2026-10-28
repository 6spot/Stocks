import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClassificationCard } from "@/components/classification-card";
import type { ProcessingStage } from "@/api/schemas";

const completedStages: ProcessingStage[] = [
  { stage: "classify", status: "completed", attempts: 1, wait_reason: null, error: null },
];

const failedStage: ProcessingStage[] = [
  {
    stage: "classify",
    status: "failed",
    attempts: 3,
    wait_reason: null,
    error: { code: "CLASSIFICATION_REJECTED", message: "分类模型返回不可用" },
  },
];

const entityClassification = {
  category: "company",
  event_type: "partnership",
  topics: ["ai_chips"],
  related_industries: ["semiconductors"],
  regions: ["US"],
  companies: ["Oracle", "OpenAI"],
  symbols: ["ORCL"],
  should_extract: true,
  reason: "The article explicitly names Oracle and OpenAI.",
  taxonomy_version: "2026-10-27",
  classified_at: "2026-10-27T19:40:00Z",
  reason_or_evidence_ref: {
    entity_evidence: [
      { kind: "company", value: "Oracle", quote: "Oracle integrates ChatGPT" },
      { kind: "symbol", value: "ORCL", quote: "tickers ORCL and GOOGL" },
    ],
    input_characters: 4800,
    truncated: true,
  },
  attempts: [
    {
      role: "classification",
      status: "succeeded",
      reported_model_name: "jev-1.14.0",
    },
    {
      role: "classification_entities",
      status: "succeeded",
      reported_model_name: "deepseek-v4.1-flash",
    },
  ],
};

describe("ClassificationCard", () => {
  it("renders labels, entities, evidence quotes and model identity", () => {
    render(
      <ClassificationCard
        classification={entityClassification}
        stages={completedStages}
      />,
    );
    expect(screen.getByText("company")).toBeInTheDocument();
    expect(screen.getByText("partnership")).toBeInTheDocument();
    expect(screen.getAllByText("Oracle").length).toBeGreaterThan(0);
    expect(screen.getAllByText("OpenAI").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ORCL").length).toBeGreaterThan(0);
    // Evidence quotes are shown verbatim per entity.
    expect(screen.getByText(/Oracle integrates ChatGPT/)).toBeInTheDocument();
    expect(screen.getByText(/tickers ORCL and GOOGL/)).toBeInTheDocument();
    // Two distinct model roles stay distinguishable; no invented models.
    expect(screen.getByText(/标签判断 jev-1.14.0 成功/)).toBeInTheDocument();
    expect(
      screen.getByText(/实体抽取 deepseek-v4.1-flash 成功/),
    ).toBeInTheDocument();
    expect(screen.getByText(/2026-10-27/)).toBeInTheDocument();
    // Background labels carry the non-mention caveat.
    expect(
      screen.getByText(/背景相关性标签，不表示文中提及公司或代码/),
    ).toBeInTheDocument();
    expect(screen.getByText(/分类输入已截断（4800 字符）/)).toBeInTheDocument();
  });

  it("does not infer symbols or companies when none were extracted", () => {
    render(
      <ClassificationCard
        classification={{
          ...entityClassification,
          companies: [],
          symbols: [],
          reason_or_evidence_ref: { entity_evidence: [] },
        }}
        stages={completedStages}
      />,
    );
    expect(screen.getByText("无（未从原文抽出公司名）")).toBeInTheDocument();
    expect(
      screen.getByText(/不从公司名或行业推断/),
    ).toBeInTheDocument();
    expect(screen.queryByText("ORCL")).not.toBeInTheDocument();
  });

  it("shows failure state when no classification was ever stored", () => {
    render(<ClassificationCard classification={null} stages={failedStage} />);
    expect(screen.getByText(/分类处理失败/)).toBeInTheDocument();
    expect(screen.getByText(/分类模型返回不可用/)).toBeInTheDocument();
    expect(screen.getByText(/此前没有已保存的分类结果/)).toBeInTheDocument();
  });

  it("keeps a stored result readable and marked when a retry failed", () => {
    render(
      <ClassificationCard
        classification={entityClassification}
        stages={failedStage}
      />,
    );
    expect(screen.getByText("最新一次分类尝试失败")).toBeInTheDocument();
    expect(screen.getByText(/未被失败尝试覆盖/)).toBeInTheDocument();
    // The old result stays fully readable.
    expect(screen.getAllByText("Oracle").length).toBeGreaterThan(0);
    expect(screen.getByText(/Oracle integrates ChatGPT/)).toBeInTheDocument();
  });

  it("shows waiting state for pending classification", () => {
    render(
      <ClassificationCard
        classification={null}
        stages={[
          {
            stage: "classify",
            status: "pending",
            attempts: 0,
            wait_reason: "requires_classification",
            error: null,
          },
        ]}
      />,
    );
    expect(screen.getByText(/分类尚未完成，等待后台处理/)).toBeInTheDocument();
    expect(screen.getByText(/requires classification/)).toBeInTheDocument();
  });

  it("marks entities as model judgment, not verified fact", () => {
    render(
      <ClassificationCard
        classification={entityClassification}
        stages={completedStages}
      />,
    );
    const section = screen.getByLabelText("分类与实体");
    expect(
      within(section).getByText(/不是已核实的投资事实/),
    ).toBeInTheDocument();
  });
});
