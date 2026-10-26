import { Link } from "react-router-dom";
import { Play, TimerReset } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/api/client";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatValue } from "@/lib/format";
import { useNoticeStore } from "@/state/ui-store";

export function TasksPage() {
  const tasks = useResource((signal) => dashboardApi.tasks(signal), []);
  const notify = useNoticeStore((state) => state.notify);
  const [pending, setPending] = useState<string | null>(null);

  async function trigger(taskId: string) {
    setPending(taskId);
    try {
      const run = await dashboardApi.triggerTask(taskId);
      if (run.created === false) {
        notify(
          `任务已有${run.status === "running" ? "运行中" : "排队中"}的 Run，未重复触发。`,
          "info",
        );
      } else {
        notify("任务已排队，Worker 将按占用规则执行。", "success");
      }
      tasks.refresh();
    } catch (error) {
      notify(
        error instanceof ApiError ? error.message : "任务触发失败。",
        "error",
      );
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="任务"
        description="任务定义保持在文件中；这里只读取当前配置、显示已有 Run 并执行手动触发。"
      />

      {tasks.data?.configuration_error?.message ? (
        <ErrorPanel
          title="任务配置存在错误"
          message={tasks.data.configuration_error.message}
        />
      ) : null}

      {tasks.loading ? (
        <LoadingPanel label="正在读取任务" />
      ) : tasks.error ? (
        <ErrorPanel message={tasks.error.message} onRetry={tasks.refresh} />
      ) : !tasks.data?.items.length ? (
        <EmptyPanel
          title="尚未配置任务"
          description="在 config/tasks.yaml 中添加任务后，这里会显示定义和最近运行。"
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>任务</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>启用</TableHead>
              <TableHead>策略</TableHead>
              <TableHead>最近 Run</TableHead>
              <TableHead className="hidden text-right sm:table-cell">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.data.items.map((task) => {
              const active = task.active_run;
              return (
                <TableRow key={task.task_id}>
                  <TableCell className="max-w-56">
                    <p className="truncate font-medium">{task.name}</p>
                    <p className="font-code text-xs text-muted-foreground">
                      {task.task_id}
                    </p>
                    <details className="mt-1 text-xs text-muted-foreground">
                      <summary className="min-h-11 cursor-pointer py-3 font-medium text-foreground sm:min-h-0 sm:py-0">
                        研究范围
                      </summary>
                      <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-muted p-2 whitespace-pre-wrap">
                        {formatValue(task.scope)}
                      </pre>
                      <p className="mt-1 flex items-center gap-1">
                        <TimerReset aria-hidden="true" className="size-3.5" />
                        {task.updated_at
                          ? `配置更新于 ${formatDateTime(task.updated_at)}`
                          : "没有持久更新时间"}
                      </p>
                    </details>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{task.type}</Badge>
                    {task.configured ? (
                      <Badge variant="success" className="ml-1.5">
                        已配置
                      </Badge>
                    ) : (
                      <Badge variant="warning" className="ml-1.5">
                        仅历史定义
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-sm">
                    {task.enabled ? "是" : "否"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {task.strategy ?? "未设置"}
                  </TableCell>
                  <TableCell className="text-xs">
                    {active ? (
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <StatusBadge status={active.status} />
                          <span className="text-warning-text">活跃 Run</span>
                        </div>
                        <p className="font-code text-muted-foreground">
                          {active.run_id}
                        </p>
                        <p className="text-muted-foreground">
                          创建于 {formatDateTime(active.created_at)}。服务端会复用此
                          Run，不会重复发起。
                        </p>
                        <Link
                          to={`/runs?run=${active.run_id}`}
                          className="font-medium text-primary underline-offset-4 hover:underline"
                        >
                          查看运行
                        </Link>
                      </div>
                    ) : task.last_run ? (
                      <div className="space-y-1">
                        <StatusBadge status={task.last_run.status} />
                        <p className="text-muted-foreground">
                          {formatDateTime(task.last_run.created_at)} ·{" "}
                          {task.last_run.step_count ?? 0} 个步骤
                        </p>
                        <Link
                          to={`/runs?run=${task.last_run.run_id}`}
                          className="font-medium text-primary underline-offset-4 hover:underline"
                        >
                          查看详情
                        </Link>
                      </div>
                    ) : (
                      <span className="text-muted-foreground">从未运行</span>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-right sm:table-cell">
                    <Button
                      type="button"
                      size="sm"
                      disabled={
                        pending === task.task_id ||
                        Boolean(active) ||
                        !task.enabled ||
                        !task.configured
                      }
                      onClick={() => void trigger(task.task_id)}
                    >
                      <Play aria-hidden="true" />
                      {active
                        ? "已有活跃 Run"
                        : pending === task.task_id
                          ? "触发中…"
                          : "手动触发"}
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
