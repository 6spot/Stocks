import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RunDetail } from "@/api/schemas";
import { RunActions } from "@/features/runs/run-actions";

const { endMock, retryMock, continueMock, notifyMock } = vi.hoisted(() => ({
  endMock: vi.fn(),
  retryMock: vi.fn(),
  continueMock: vi.fn(),
  notifyMock: vi.fn(),
}));

vi.mock("@/api/dashboard", () => ({
  dashboardApi: {
    endRun: endMock,
    retryRun: retryMock,
    continueRun: continueMock,
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

const run = {
  run_id: "run-1",
  task_id: "research",
  status: "running",
  actions: { can_retry: false, can_continue: false, can_end: true },
} as RunDetail;

describe("RunActions", () => {
  beforeEach(() => {
    endMock.mockReset();
    notifyMock.mockReset();
  });

  it("sends confirm_stopped only after the Owner checks the confirmation", async () => {
    endMock.mockResolvedValue({ ...run, status: "cancelled" });
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<RunActions run={run} onChanged={onChanged} />);

    await user.click(
      screen.getByRole("button", { name: "结束异常 Run" }),
    );
    expect(
      screen.getByRole("button", { name: "确认并结束" }),
    ).toBeDisabled();
    expect(endMock).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole("checkbox", { name: /我已确认旧进程已停止/ }),
    );
    await user.click(screen.getByRole("button", { name: "确认并结束" }));
    expect(endMock).toHaveBeenCalledWith("run-1", true);
    expect(onChanged).toHaveBeenCalled();
  });
});
