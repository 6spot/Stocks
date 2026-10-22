import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { ApiError } from "@/api/client";
import { dashboardApi } from "@/api/dashboard";
import type { ProcessingRetryResult } from "@/api/schemas";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { ConfirmStopDialog } from "@/components/confirm-stop-dialog";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, shortId } from "@/lib/format";
import { stageLabel } from "@/lib/labels";
import { useNoticeStore } from "@/state/ui-store";

const stageOptions = ["parse", "classify", "facts", "index"];
const statusOptions = ["failed", "pending", "running", "completed"];

function resultErrors(result: ProcessingRetryResult): string[] {
  return result.errors.map((entry) => {
    const detail = entry.error?.message ?? entry.error?.code ?? "阶段未被接受";
    return `${stageLabel(entry.stage)}：${detail}`;
  });
}

export function ProcessingPanel() {
  const [stage, setStage] = useState("");
  const [status, setStatus] = useState("failed");
  const [pendingDocument, setPendingDocument] = useState<string | null>(null);
  const [pendingStage, setPendingStage] = useState<string | null>(null);
  const [stopRequest, setStopRequest] = useState<{
    documentId: string;
    stages: string[];
  } | null>(null);
  const notify = useNoticeStore((state) => state.notify);
  const processing = useResource(
    (signal) =>
      dashboardApi.processing(
        {
          stage: stage || null,
          status: status || null,
          limit: 100,
        },
        signal,
      ),
    [stage, status],
  );

  async function requestRetry(
    documentId: string,
    stages: string[],
    confirmStopped = false,
  ) {
    setPendingDocument(documentId);
    setPendingStage(stages.join(","));
    try {
      const result = await dashboardApi.retryProcessing(
        documentId,
        stages,
        confirmStopped,
      );
      if (result.accepted) {
        notify("阶段已重新排队，等待 Worker 执行。", "success");
        setStopRequest(null);
        processing.refresh();
        return;
      }
      const details = resultErrors(result);
      const needsConfirmation = result.errors.some(
        (entry) => entry.error?.code === "OWNER_CONFIRMATION_REQUIRED",
      );
      if (needsConfirmation && !confirmStopped) {
        setStopRequest({ documentId, stages });
        return;
      }
      notify(
        details[0] ?? "服务端返回 accepted=false，阶段未排队。",
        "error",
      );
    } catch (error) {
      notify(
        error instanceof ApiError ? error.message : "处理阶段重试失败。",
        "error",
      );
    } finally {
      setPendingDocument(null);
      setPendingStage(null);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>处理积压</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1 text-xs font-medium">
              阶段
              <select
                className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
                value={stage}
                onChange={(event) => setStage(event.target.value)}
              >
                <option value="">全部可用阶段</option>
                {stageOptions.map((item) => (
                  <option key={item} value={item}>
                    {stageLabel(item)}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-xs font-medium">
              状态
              <select
                className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="">全部状态</option>
                {statusOptions.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {processing.loading ? (
            <LoadingPanel label="正在读取处理积压" />
          ) : processing.error ? (
            <ErrorPanel
              message={processing.error.message}
              onRetry={processing.refresh}
            />
          ) : !processing.data?.items.length ? (
            <EmptyPanel
              title="当前筛选没有积压"
              description="这不代表所有 Document 都可搜索；请切换阶段或状态检查。"
            />
          ) : (
            <div className="space-y-3">
              {processing.data.items.map((item) => {
                const isPending =
                  pendingDocument === item.document_id &&
                  pendingStage === item.stage;
                return (
                  <div
                    key={`${item.document_id}-${item.stage}`}
                    className="rounded-xl border border-border p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="min-w-0 flex-1 truncate font-medium">
                        {item.document?.title ?? item.document_id}
                      </p>
                      <Badge variant="outline">{stageLabel(item.stage)}</Badge>
                      <StatusBadge status={item.status} />
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Document{" "}
                      <span className="font-code">
                        {shortId(item.document_id)}
                      </span>{" "}
                      · 尝试 {item.attempts}/{item.max_attempts} · 最近开始{" "}
                      {formatDateTime(item.started_at)}
                    </p>
                    {item.next_attempt_at ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        下次尝试 {formatDateTime(item.next_attempt_at)}
                      </p>
                    ) : null}
                    {item.wait_reason ? (
                      <p className="mt-1 text-xs text-amber-800">
                        等待：{item.wait_reason.replaceAll("_", " ")}
                      </p>
                    ) : null}
                    {item.skip_reason ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        跳过：{item.skip_reason.replaceAll("_", " ")}
                      </p>
                    ) : null}
                    {item.error?.message || item.error?.code ? (
                      <p className="mt-2 text-xs text-destructive">
                        {item.error.message ?? item.error.code}
                      </p>
                    ) : null}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={
                          pendingDocument !== null ||
                          item.status === "completed" ||
                          item.status === "skipped" ||
                          item.processor_available === false
                        }
                        onClick={() =>
                          void requestRetry(item.document_id, [item.stage])
                        }
                      >
                        <RefreshCw aria-hidden="true" />
                        {isPending ? "排队中…" : "重试此阶段"}
                      </Button>
                      {item.status === "running" ? (
                        <span className="text-xs text-amber-800">
                          running 阶段需要先确认旧进程停止
                        </span>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmStopDialog
        open={stopRequest !== null}
        title="释放处理阶段所有权"
        description="该阶段仍标记为运行中。确认旧进程停止后，才能重新打开并排队。"
        confirmLabel="确认停止并重试"
        pending={pendingDocument !== null}
        onOpenChange={(open) => {
          if (!open) {
            setStopRequest(null);
          }
        }}
        onConfirm={() => {
          if (stopRequest) {
            void requestRetry(
              stopRequest.documentId,
              stopRequest.stages,
              true,
            );
            setStopRequest(null);
          }
        }}
      />
    </div>
  );
}
