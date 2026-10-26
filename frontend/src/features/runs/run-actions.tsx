import { CircleStop, RefreshCw, RotateCcw } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/api/client";
import { dashboardApi } from "@/api/dashboard";
import type { RunDetail } from "@/api/schemas";
import { ConfirmStopDialog } from "@/components/confirm-stop-dialog";
import { Button } from "@/components/ui/button";
import { useNoticeStore } from "@/state/ui-store";

export function RunActions({
  run,
  onChanged,
}: {
  run: RunDetail;
  onChanged: () => void;
}) {
  const notify = useNoticeStore((state) => state.notify);
  const [pending, setPending] = useState<
    "retry" | "continue" | "end" | null
  >(null);
  const [endOpen, setEndOpen] = useState(false);

  async function retry() {
    setPending("retry");
    try {
      const result = await dashboardApi.retryRun(run.run_id);
      notify(
        result.created === false
          ? "已有活跃 Run，服务端复用了现有记录。"
          : "重试 Run 已排队。",
        result.created === false ? "info" : "success",
      );
      onChanged();
    } catch (error) {
      notify(error instanceof ApiError ? error.message : "重试失败。", "error");
    } finally {
      setPending(null);
    }
  }

  async function continueScan() {
    setPending("continue");
    try {
      const result = await dashboardApi.continueRun(run.run_id);
      notify(
        result.created === false
          ? "已有同一任务的活跃 Run，已显示现有记录。"
          : "未完成扫描已继续排队。",
        result.created === false ? "info" : "success",
      );
      onChanged();
    } catch (error) {
      notify(
        error instanceof ApiError ? error.message : "继续扫描失败。",
        "error",
      );
    } finally {
      setPending(null);
    }
  }

  async function end() {
    setPending("end");
    try {
      await dashboardApi.endRun(run.run_id, true);
      notify("异常 Run 已结束。可继续执行重试或续扫。", "success");
      setEndOpen(false);
      onChanged();
    } catch (error) {
      notify(error instanceof ApiError ? error.message : "结束 Run 失败。", "error");
    } finally {
      setPending(null);
    }
  }

  if (!run.actions.can_retry && !run.actions.can_continue && !run.actions.can_end) {
    return (
      <p className="text-xs text-muted-foreground">
        当前 Run 状态没有可用的恢复动作。
      </p>
    );
  }

  return (
    <>
      <div className="hidden flex-wrap gap-2 sm:flex">
        {run.actions.can_retry ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={pending !== null}
            onClick={() => void retry()}
          >
            <RotateCcw aria-hidden="true" />
            {pending === "retry" ? "排队中…" : "重试失败步骤"}
          </Button>
        ) : null}
        {run.actions.can_continue ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={pending !== null}
            onClick={() => void continueScan()}
          >
            <RefreshCw aria-hidden="true" />
            {pending === "continue" ? "排队中…" : "继续未完成扫描"}
          </Button>
        ) : null}
        {run.actions.can_end ? (
          <Button
            type="button"
            variant="destructive"
            size="sm"
            disabled={pending !== null}
            onClick={() => setEndOpen(true)}
          >
            <CircleStop aria-hidden="true" />
            结束异常 Run
          </Button>
        ) : null}
      </div>
      <ConfirmStopDialog
        open={endOpen}
        title="结束异常 Run"
        description="此操作会把 Run 标记为已结束。只有确认拥有该 Run 的旧进程已经停止时才能继续。"
        confirmLabel="确认并结束"
        pending={pending === "end"}
        onOpenChange={setEndOpen}
        onConfirm={() => void end()}
      />
    </>
  );
}
