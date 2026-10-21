import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessingPanel } from "@/features/runs/processing-panel";

const { processingMock, retryMock, notifyMock } = vi.hoisted(() => ({
  processingMock: vi.fn(),
  retryMock: vi.fn(),
  notifyMock: vi.fn(),
}));

vi.mock("@/api/dashboard", () => ({
  dashboardApi: {
    processing: processingMock,
    retryProcessing: retryMock,
  },
}));

vi.mock("@/state/ui-store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/state/ui-store")>();
  return {
    ...actual,
    useNoticeStore: (
      selector: (state: { notify: typeof notifyMock }) => unknown,
    ) => selector({ notify: notifyMock }),
  };
});

const pendingItem = {
  document_id: "d1",
  stage: "parse",
  status: "failed",
  attempts: 1,
  max_attempts: 3,
  next_attempt_at: null,
  started_at: "2026-10-20T01:00:00Z",
  finished_at: "2026-10-20T01:01:00Z",
  error: { code: "PARSE_FAILED", message: "Parse failed" },
  wait_reason: null,
  skip_reason: null,
  processor_available: true,
  document: {
    id: "d1",
    source_id: "sec",
    dedupe_key: "x",
    version: 1,
    title: "A document",
    collected_at: "2026-10-20T00:00:00Z",
    completeness: "complete",
  },
};

describe("ProcessingPanel", () => {
  beforeEach(() => {
    processingMock.mockReset();
    retryMock.mockReset();
    notifyMock.mockReset();
    processingMock.mockResolvedValue({
      items: [pendingItem],
      next_cursor: null,
      has_more: false,
      limit: 1,
      available_stages: ["parse", "classify", "facts", "index"],
      counts: { by_status: {}, by_stage: {} },
    });
  });

  it("shows accepted=false errors returned with HTTP 200", async () => {
    retryMock.mockResolvedValue({
      document_id: "d1",
      accepted: false,
      executions: [],
      errors: [
        {
          document_id: "d1",
          stage: "parse",
          error: {
            code: "OWNER_CONFIRMATION_REQUIRED",
            message: "Confirm the old process stopped",
          },
        },
      ],
    });
    const user = userEvent.setup();
    render(<ProcessingPanel />);

    await user.click(
      await screen.findByRole("button", { name: "重试此阶段" }),
    );
    await waitFor(() =>
      expect(retryMock).toHaveBeenCalledWith("d1", ["parse"], false),
    );
    expect(
      screen.getByRole("dialog", { name: "释放处理阶段所有权" }),
    ).toBeVisible();

    await user.click(
      screen.getByRole("checkbox", { name: /我已确认旧进程已停止/ }),
    );
    await user.click(
      screen.getByRole("button", { name: "确认停止并重试" }),
    );
    await waitFor(() =>
      expect(retryMock).toHaveBeenLastCalledWith("d1", ["parse"], true),
    );
  });

  it("surfaces a non-confirmation rejection to the Owner", async () => {
    retryMock.mockResolvedValue({
      document_id: "d1",
      accepted: false,
      executions: [],
      errors: [
        {
          document_id: "d1",
          stage: "parse",
          error: {
            code: "STAGE_ALREADY_COMPLETED",
            message: "Stage is already completed",
          },
        },
      ],
    });
    const user = userEvent.setup();
    render(<ProcessingPanel />);

    await user.click(
      await screen.findByRole("button", { name: "重试此阶段" }),
    );
    await waitFor(() =>
      expect(notifyMock).toHaveBeenCalledWith(
        "解析：Stage is already completed",
        "error",
      ),
    );
  });
});
