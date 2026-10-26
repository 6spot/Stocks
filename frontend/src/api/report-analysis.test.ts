import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest } from "@/api/client";
import { reportAnalysisSchema } from "@/api/schemas";

const reference = { document_id: "d1", chunk_id: "c1" };
const finding = {
  id: "growth",
  title: "Growth needs cash flow confirmation",
  kind: "model_inference",
  statement: "Revenue increased.",
  rationale: "Revenue and cash flow need to be considered together.",
  uncertainty: "A single period cannot establish persistence.",
  evidence_refs: [reference],
};
const section = {
  heading: "Growth quality",
  body: "The available evidence supports a preliminary assessment.",
  finding_ids: [finding.id],
};
const analysis = {
  title: "Growth quality remains uncertain",
  summary: "Cash conversion remains a key uncertainty.",
  sections: [section],
  findings: [finding],
  open_questions: ["Will cash conversion improve?"],
  evidence_refs: [reference],
  created_at: "2026-10-20T05:00:00Z",
};

afterEach(() => vi.unstubAllGlobals());

describe("report analysis schema", () => {
  it("accepts finding reuse across chapters and preserves repeated finding references", () => {
    const input = {
      ...analysis,
      sections: [section, { ...section, heading: "Outlook" }],
      findings: [{ ...finding, evidence_refs: [reference, reference] }],
    };
    expect(reportAnalysisSchema.parse(input)).toEqual(input);
  });

  it("accepts an article without evidence, findings or open questions", () => {
    const input = {
      ...analysis,
      sections: [{ ...section, finding_ids: [] }],
      findings: [],
      open_questions: [],
      evidence_refs: [],
    };
    expect(reportAnalysisSchema.parse(input)).toEqual(input);
  });

  it.each(["", " \n\t"])("rejects blank article text %j", (text) => {
    for (const field of ["title", "summary"]) {
      expect(reportAnalysisSchema.safeParse({ ...analysis, [field]: text }).success).toBe(false);
    }
    for (const field of ["heading", "body"]) {
      expect(reportAnalysisSchema.safeParse({
        ...analysis, sections: [{ ...section, [field]: text }],
      }).success).toBe(false);
    }
    for (const field of ["id", "title", "statement", "rationale", "uncertainty"]) {
      expect(reportAnalysisSchema.safeParse({
        ...analysis,
        findings: [{ ...finding, [field]: text }],
        sections: [{ ...section, finding_ids: [field === "id" ? text : finding.id] }],
      }).success).toBe(false);
    }
    expect(reportAnalysisSchema.safeParse({ ...analysis, open_questions: [text] }).success).toBe(false);
  });

  it("requires at least one chapter and at least one reference per finding", () => {
    expect(reportAnalysisSchema.safeParse({ ...analysis, sections: [], findings: [] }).success).toBe(false);
    expect(reportAnalysisSchema.safeParse({
      ...analysis, findings: [{ ...finding, evidence_refs: [] }],
    }).success).toBe(false);
  });

  it.each([
    ["duplicate finding IDs", { ...analysis, findings: [finding, finding] }],
    ["unknown chapter links", { ...analysis, sections: [{ ...section, finding_ids: [finding.id, "unknown"] }] }],
    ["repeated chapter links", { ...analysis, sections: [{ ...section, finding_ids: [finding.id, finding.id] }] }],
    ["unused findings", { ...analysis, sections: [{ ...section, finding_ids: [] }] }],
  ])("rejects %s instead of silently discarding links", (_name, input) => {
    expect(reportAnalysisSchema.safeParse(input).success).toBe(false);
  });

  it("reports invalid associations through the existing API schema error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...analysis,
      sections: [{ ...section, finding_ids: [finding.id, "unknown"] }],
    }), { headers: { "Content-Type": "application/json" } })));
    await expect(apiRequest("/api/reports/r1", {
      schema: reportAnalysisSchema,
    })).rejects.toMatchObject({
      code: "API_RESPONSE_INVALID",
      message: "API 响应与当前控制台版本不兼容。",
    });
  });
});
