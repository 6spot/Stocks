import { Activity, ListFilter, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, shortId } from "@/lib/format";
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

  function closeRun() {
    const next = new URLSearchParams(params);
    next.delete("run");
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

  const runSheetOpen = Boolean(selectedRun) && params.get("tab") !== "processing";

  return (
    <div className="space-y-6">
      <PageHeader
        title="运行与恢复"
        description="定位失败步骤、模型调用和处理积压；恢复动作以服务端能力字段为准。"
      />

      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_12rem_auto]">
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
          <section aria-label="Run 列表" className="min-w-0">
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
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>任务</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>创建时间</TableHead>
                    <TableHead className="text-right">步骤</TableHead>
                    <TableHead>Run ID</TableHead>
                    <TableHead>错误摘要</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.data.items.map((run) => (
                    <TableRow
                      key={run.run_id}
                      data-state={selectedRun === run.run_id ? "selected" : undefined}
                    >
                      <TableCell className="max-w-64">
                        <button
                          type="button"
                          aria-pressed={selectedRun === run.run_id}
                          aria-label={`查看 ${run.task_id} 的 Run 详情`}
                          onClick={() => selectRun(run.run_id)}
                          className="block w-full min-w-0 cursor-pointer text-left"
                        >
                          <span className="block truncate text-sm font-medium">
                            {run.task_id}
                          </span>
                        </button>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={run.status} />
                      </TableCell>
                      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                        {formatDateTime(run.created_at)}
                      </TableCell>
                      <TableCell className="text-right text-xs tabular-nums">
                        {run.step_count ?? 0}
                      </TableCell>
                      <TableCell className="font-code text-xs text-muted-foreground">
                        {shortId(run.run_id)}
                      </TableCell>
                      <TableCell className="max-w-72 text-xs text-destructive">
                        <span className="line-clamp-2">
                          {run.error?.message ?? ""}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </section>
        </TabsContent>
        <TabsContent value="processing">
          <ProcessingPanel />
        </TabsContent>
      </Tabs>

      <Sheet
        open={runSheetOpen}
        onOpenChange={(open) => {
          if (!open) {
            closeRun();
          }
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Run 详情</SheetTitle>
            <SheetDescription>
              步骤、模型调用、错误和安全恢复操作分开显示。
            </SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1">
            {selectedRun ? (
              <RunDetailView
                key={selectedRun}
                runId={selectedRun}
                onChanged={() => {
                  runs.refresh();
                }}
              />
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
