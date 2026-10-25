import { Activity, ListFilter, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, shortId } from "@/lib/format";
import { statusLabel } from "@/lib/labels";
import { ProcessingPanel } from "@/features/runs/processing-panel";
import { RunDetailView } from "@/features/runs/run-detail-view";

export function RunsPage() {
  const [params, setParams] = useSearchParams();
  const selectedRun = params.get("run");
  const runStatus = params.get("status") ?? "";
  const taskId = params.get("task") ?? "";
  const runs = useResource(
    (signal) =>
      dashboardApi.runs(
        {
          taskId: taskId || null,
          status: runStatus || null,
          limit: 100,
        },
        signal,
      ),
    [runStatus, taskId],
  );

  function selectRun(runId: string) {
    const next = new URLSearchParams(params);
    next.set("run", runId);
    setParams(next, { replace: true });
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setParams(next, { replace: true });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Runs"
        title="运行与恢复"
        description="定位失败步骤、模型调用和处理积压；恢复动作以服务端能力字段为准。"
      />

      <div className="grid gap-3 rounded-xl border border-border bg-card/80 p-3 sm:grid-cols-[minmax(0,1fr)_12rem_auto]">
        <label className="grid gap-1 text-xs font-medium">
          任务 ID
          <Input
            value={taskId}
            placeholder="全部任务"
            onChange={(event) => setFilter("task", event.target.value)}
          />
        </label>
        <label className="grid gap-1 text-xs font-medium">
          状态
          <select
            className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
            value={runStatus}
            onChange={(event) => setFilter("status", event.target.value)}
          >
            <option value="">全部状态</option>
            <option value="queued">排队中</option>
            <option value="running">运行中</option>
            <option value="succeeded">成功</option>
            <option value="failed">失败</option>
            <option value="cancelled">已结束</option>
          </select>
        </label>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="self-end"
          disabled={!taskId && !runStatus}
          onClick={() => {
            const next = new URLSearchParams(params);
            next.delete("task");
            next.delete("status");
            setParams(next, { replace: true });
          }}
        >
          <X aria-hidden="true" />
          清除筛选
        </Button>
      </div>

      <Tabs
        value={params.get("tab") === "processing" ? "processing" : "runs"}
        onValueChange={(value) => setFilter("tab", String(value))}
      >
        <TabsList aria-label="运行页面">
          <TabsTrigger value="runs">
            <Activity aria-hidden="true" />
            Run
          </TabsTrigger>
          <TabsTrigger value="processing">
            <ListFilter aria-hidden="true" />
            处理积压
          </TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          <div className="grid gap-5 xl:grid-cols-[minmax(20rem,0.72fr)_minmax(0,1.28fr)]">
            <section className="space-y-3" aria-label="Run 列表">
              {runs.loading ? (
                <LoadingPanel label="正在读取 Run" />
              ) : runs.error ? (
                <ErrorPanel
                  message={runs.error.message}
                  onRetry={runs.refresh}
                />
              ) : !runs.data?.items.length ? (
                <EmptyPanel
                  title="当前筛选没有 Run"
                  description="任务触发后，Worker 会在此记录执行。"
                />
              ) : (
                runs.data.items.map((run) => (
                  <button
                    key={run.run_id}
                    type="button"
                    aria-pressed={selectedRun === run.run_id}
                    onClick={() => selectRun(run.run_id)}
                    className={
                      selectedRun === run.run_id
                        ? "press w-full rounded-xl border border-primary/40 bg-card p-3 text-left ring-2 ring-primary/20"
                        : "press w-full rounded-xl border border-border bg-card p-3 text-left hover:bg-muted/40"
                    }
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="min-w-0 flex-1 truncate font-medium">
                        {run.task_id}
                      </p>
                      <StatusBadge status={run.status} />
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {formatDateTime(run.created_at)} ·{" "}
                      {run.step_count ?? 0} 个步骤 ·{" "}
                      {shortId(run.run_id)}
                    </p>
                    {run.error?.message ? (
                      <p className="mt-1 line-clamp-2 text-xs text-destructive">
                        {run.error.message}
                      </p>
                    ) : null}
                  </button>
                ))
              )}
            </section>
            <section className="min-w-0">
              {selectedRun ? (
                <RunDetailView
                  key={selectedRun}
                  runId={selectedRun}
                  onChanged={() => {
                    runs.refresh();
                  }}
                />
              ) : (
                <EmptyPanel
                  title="选择一个 Run"
                  description="查看步骤、模型调用、错误和安全恢复操作。"
                />
              )}
            </section>
          </div>
        </TabsContent>
        <TabsContent value="processing">
          <ProcessingPanel />
        </TabsContent>
      </Tabs>

      {selectedRun && params.get("tab") !== "processing" ? (
        <p className="text-xs text-muted-foreground">
          当前运行状态：{statusLabel(
            runs.data?.items.find((run) => run.run_id === selectedRun)?.status,
          )}{" "}
          ·{" "}
          <button
            type="button"
            className="text-primary hover:underline"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete("run");
              setParams(next, { replace: true });
            }}
          >
            清除选择
          </button>
        </p>
      ) : null}
    </div>
  );
}
