import { Link } from "react-router-dom";
import { CircleAlert } from "lucide-react";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useResource } from "@/hooks/use-resource";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatValue,
} from "@/lib/format";
import { RunActions } from "@/features/runs/run-actions";
import { UsageSummaryCard } from "@/components/usage-summary";

/**
 * Step usage is an internal, persisted summary; read only the known numeric
 * fields and let a missing provider-reported cost stay unknown.
 */
function stepUsageLine(usage: unknown): string | null {
  if (!usage || typeof usage !== "object") {
    return null;
  }
  const record = usage as Record<string, unknown>;
  const number = (key: string): number | null =>
    typeof record[key] === "number" ? (record[key] as number) : null;
  const parts: string[] = [];
  const totalTokens = number("total_tokens");
  if (totalTokens !== null) {
    parts.push(`Tokens ${formatNumber(totalTokens)}`);
  }
  const duration = number("duration_ms");
  if (duration !== null) {
    parts.push(`耗时 ${formatDuration(duration)}`);
  }
  parts.push(`费用 ${formatCost(number("cost_usd"))}`);
  const unknownCosts = number("cost_unknown_attempts");
  if (unknownCosts) {
    parts.push(`${unknownCosts} 次未提供费用`);
  }
  return parts.join(" · ");
}

export function RunDetailView({
  runId,
  onChanged,
}: {
  runId: string;
  onChanged?: () => void;
}) {
  const run = useResource(
    (signal) => dashboardApi.run(runId, signal),
    [runId],
  );

  if (run.loading) {
    return <LoadingPanel label="正在读取 Run 详情" />;
  }
  if (run.error) {
    return <ErrorPanel message={run.error.message} onRetry={run.refresh} />;
  }
  if (!run.data) {
    return <EmptyPanel title="没有 Run 详情" />;
  }
  const data = run.data;
  return (
    <article className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-lg">{data.task_id}</CardTitle>
            <StatusBadge status={data.status} />
            {data.retry_of_run_id ? (
              <Badge variant="outline">重试 Run</Badge>
            ) : null}
          </div>
          <CardDescription className="font-code break-all">
            {data.run_id}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">创建</dt>
              <dd>{formatDateTime(data.created_at)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">开始</dt>
              <dd>{formatDateTime(data.started_at)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">结束</dt>
              <dd>{formatDateTime(data.finished_at)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">触发方式</dt>
              <dd>{data.triggered_by ?? "未记录"}</dd>
            </div>
          </dl>
          {data.change_scan ? (
            <div className="rounded-lg border border-border bg-muted/30 p-3 text-xs">
              <p className="font-medium">研究增量窗口</p>
              <p className="mt-1 text-muted-foreground">
                {formatDateTime(data.change_scan.window_start)} –{" "}
                {formatDateTime(data.change_scan.window_end)}
                {" · "}
                扫描完成：
                {data.change_scan.change_scan_completed === null
                  ? "未记录"
                  : data.change_scan.change_scan_completed
                    ? "是"
                    : "否"}
              </p>
            </div>
          ) : null}
          {data.error?.message || data.error?.code ? (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive">
              <div className="flex items-center gap-2 font-medium">
                <CircleAlert aria-hidden="true" className="size-4" />
                安全错误
              </div>
              <p className="mt-1">
                {data.error.message ?? data.error.code}
              </p>
            </div>
          ) : null}
          <RunActions
            run={data}
            onChanged={() => {
              run.refresh();
              onChanged?.();
            }}
          />
          {data.report ? (
            <Link
              to={`/research?report=${data.report.report_id}`}
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              打开此 Run 生成的报告
            </Link>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>步骤</CardTitle>
          <CardDescription>
            只显示服务端去敏后的定位、状态和安全错误。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {data.steps.length ? (
            <div className="space-y-3">
              {data.steps.map((step) => (
                <div
                  key={step.step_id}
                  className="rounded-lg border border-border p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">#{step.position}</Badge>
                    <p className="min-w-0 flex-1 font-medium">{step.type}</p>
                    <StatusBadge status={step.status} />
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {step.step_id} · {formatDateTime(step.created_at)}
                  </p>
                  {stepUsageLine(step.usage) ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {stepUsageLine(step.usage)}
                    </p>
                  ) : null}
                  {step.error?.message || step.error?.code ? (
                    <p className="mt-2 text-xs text-destructive">
                      {step.error.message ?? step.error.code}
                    </p>
                  ) : null}
                  {step.output !== null && step.output !== undefined ? (
                    <details className="mt-3 text-xs">
                      <summary className="cursor-pointer font-medium">
                        安全输出
                      </summary>
                      <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-muted p-3 whitespace-pre-wrap">
                        {formatValue(step.output)}
                      </pre>
                    </details>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <EmptyPanel
              title="尚未记录步骤"
              description="Run 可能仍在等待 Worker 领取。"
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>模型调用</CardTitle>
          <CardDescription>
            显示实际 Provider、模型、耗时、用量、供应商提供的费用与回退原因。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {data.model_calls.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>步骤 / 尝试</TableHead>
                  <TableHead>模型</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>耗时</TableHead>
                  <TableHead>Tokens</TableHead>
                  <TableHead>费用</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.model_calls.map((call, index) => (
                  <TableRow key={`${call.step_id}-${call.attempt_number}-${index}`}>
                    <TableCell className="font-code text-xs">
                      {call.step_id} / {call.attempt_number}
                    </TableCell>
                    <TableCell className="text-xs">
                      {call.reported_model_name ??
                        call.attempt_model_name ??
                        call.attempt_alias ??
                        "未记录"}
                      <span className="mt-1 block text-muted-foreground">
                        Provider {call.provider ?? "未记录"}
                      </span>
                      {call.fallback_reason ? (
                        <span className="mt-1 block text-amber-700">
                          回退：{call.fallback_reason}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={call.status} />
                    </TableCell>
                    <TableCell>
                      {formatDuration(call.duration_ms)}
                    </TableCell>
                    <TableCell>
                      {formatNumber(call.total_tokens)}
                      <span className="block text-xs text-muted-foreground">
                        {formatNumber(call.prompt_tokens)} +{" "}
                        {formatNumber(call.completion_tokens)}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      {formatCost(call.cost_usd)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyPanel title="没有模型调用记录" />
          )}
        </CardContent>
      </Card>

      <UsageSummaryCard
        usage={data.usage}
        description="按本次 Run 已保存的模型调用汇总；未提供费用的调用保持未知，不按 0 计算。"
        footer={
          data.report ? (
            <Link
              to={`/research?report=${data.report.report_id}`}
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              查看此 Run 的覆盖与报告
            </Link>
          ) : null
        }
      />
    </article>
  );
}
