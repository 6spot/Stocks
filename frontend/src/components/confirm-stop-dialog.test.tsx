import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmStopDialog } from "@/components/confirm-stop-dialog";

describe("ConfirmStopDialog", () => {
  it("requires an explicit stopped-process confirmation before ending", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConfirmStopDialog
        open
        title="结束 Run"
        description="确认旧进程已停止"
        confirmLabel="确认并结束"
        onOpenChange={() => undefined}
        onConfirm={onConfirm}
      />,
    );

    const confirm = screen.getByRole("button", { name: "确认并结束" });
    expect(confirm).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", { name: /我已确认旧进程已停止/ }),
    );
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
