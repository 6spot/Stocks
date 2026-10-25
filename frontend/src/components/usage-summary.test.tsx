import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { UsageSummary } from "@/api/schemas";
import { UsageModels, UsageStats } from "@/components/usage-summary";

describe("UsageStats", () => {
  it("shows the reported cost and keeps an unreported cost unknown", () => {
    render(
      <UsageStats
        usage={
          {
            call_count: 2,
            succeeded_calls: 2,
            failed_calls: 0,
            fallback_calls: 0,
            prompt_tokens: 120,
            completion_tokens: 60,
            total_tokens: 180,
            duration_ms: 2000,
            cost_usd: 0.0025,
            cost_known_calls: 1,
            cost_unknown_calls: 1,
            models: [
              {
                provider: "default",
                model_name: "research-model",
                calls: 1,
                total_tokens: 150,
                cost_usd: 0.0025,
                cost_unknown_calls: 0,
              },
              {
                provider: "default",
                model_name: "cheap-model",
                calls: 1,
                total_tokens: 30,
                cost_usd: null,
                cost_unknown_calls: 1,
              },
            ],
            models_truncated: false,
          } as UsageSummary
        }
      />,
    );

    expect(screen.getByText("$0.0025")).toBeVisible();
    expect(screen.getByText("1 次调用未提供费用")).toBeVisible();
    // An unreported cost is never rendered as a zero amount.
    expect(screen.queryByText("$0")).toBeNull();
  });

  it("keeps a per-model unreported cost unknown in the model list", () => {
    render(
      <UsageModels
        usage={
          {
            call_count: 2,
            succeeded_calls: 2,
            failed_calls: 0,
            fallback_calls: 0,
            prompt_tokens: 120,
            completion_tokens: 60,
            total_tokens: 180,
            duration_ms: 2000,
            cost_usd: 0.0025,
            cost_known_calls: 1,
            cost_unknown_calls: 1,
            models: [
              {
                provider: "default",
                model_name: "research-model",
                calls: 1,
                total_tokens: 150,
                cost_usd: 0.0025,
                cost_unknown_calls: 0,
              },
              {
                provider: "default",
                model_name: "cheap-model",
                calls: 1,
                total_tokens: 30,
                cost_usd: null,
                cost_unknown_calls: 1,
              },
            ],
            models_truncated: false,
          } as UsageSummary
        }
      />,
    );

    expect(screen.getByText(/cheap-model/)).toHaveTextContent(
      "费用 未知（1 次未提供费用）",
    );
    // An unreported cost is never rendered as a zero amount.
    expect(screen.queryByText("$0")).toBeNull();
  });
});
