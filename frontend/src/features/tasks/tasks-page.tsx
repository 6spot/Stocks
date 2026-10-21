import { Link } from "react-router-dom";
import { Play, TimerReset } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/api/client";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
        eyebrow="Tasks"
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
        <div className="grid gap-4 xl:grid-cols-2">
          {tasks.data.items.map((task) => {
            const active = task.active_run;
            return (
              <Card key={task.task_id}>
                <CardHeader>
                  <div className="flex flex-wrap items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <CardTitle className="text-lg">{task.name}</CardTitle>
                      <CardDescription className="mt-1 font-code">
                        {task.task_id}
                      </CardDescription>
                    </div>
                    <Badge variant="outline">{task.type}</Badge>
                    {task.configured ? (
                      <Badge variant="success">已配置</Badge>
                    ) : (
                      <Badge variant="warning">仅历史定义</Badge>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <dl className="grid gap-2 text-xs sm:grid-cols-2">
                    <div>
                      <dt className="text-muted-foreground">启用</dt>
                      <dd>{task.enabled ? "是" : "否"}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">策略</dt>
                      <dd>{task.strategy ?? "未设置"}</dd>
                    </div>
                    <div className="sm:col-span-2">
                      <dt className="text-muted-foreground">研究范围</dt>
                      <dd>
                        <pre className="mt-1 max-h-32 overflow-auto rounded-lg bg-muted p-2 whitespace-pre-wrap">
                          {formatValue(task.scope)}
                        </pre>
                      </dd>
                    </div>
                  </dl>

                  {active ? (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium">已有活跃 Run</span>
                        <StatusBadge status={active.status} />
                      </div>
                      <p className="mt-2 font-code text-xs">{active.run_id}</p>
                      <p className="mt-1 text-xs">
                        创建于 {formatDateTime(active.created_at)}。服务端会复用此
                        Run，不会重复发起。
                      </p>
                      <Link
                        to={`/runs?run=${active.run_id}`}
                        className={buttonVariants({
                          variant: "outline",
                          size: "sm",
                          className: "mt-3",
                        })}
                      >
                        查看运行
                      </Link>
                    </div>
                  ) : (
                    <div className="rounded-xl border border-border p-3 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-muted-foreground">最近 Run</span>
                        {task.last_run ? (
                          <StatusBadge status={task.last_run.status} />
                        ) : (
                          <Badge variant="outline">从未运行</Badge>
                        )}
                      </div>
                      {task.last_run ? (
                        <>
                          <p className="mt-2 text-xs">
                            {formatDateTime(task.last_run.created_at)} ·{" "}
                            {task.last_run.step_count ?? 0} 个步骤
                          </p>
                          <Link
                            to={`/runs?run=${task.last_run.run_id}`}
                            className={buttonVariants({
                              variant: "ghost",
                              size: "sm",
                              className: "mt-2",
                            })}
                          >
                            查看详情
                          </Link>
                        </>
                      ) : (
                        <p className="mt-2 text-xs text-muted-foreground">
                          还没有执行记录。
                        </p>
                      )}
                    </div>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <TimerReset aria-hidden="true" className="size-3.5" />
                      {task.updated_at
                        ? `配置更新于 ${formatDateTime(task.updated_at)}`
                        : "没有持久更新时间"}
                    </p>
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
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
