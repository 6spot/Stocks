import { Link } from "react-router-dom";
import { CircleAlert } from "lucide-react";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useResource } from "@/hooks/use-resource";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatValue,
  shortId,
} from "@/lib/format";
import { RunActions } from "@/features/runs/run-actions";
import { Section } from "@/components/section";
import { UsageSummarySection } from "@/components/usage-summary";
import { statusLabel } from "@/lib/labels";

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

/**
 * Current collection pointers live with the Run, not the report reading view.
 * The report detail is fetched lazily only when this section mounts, so
 * opening a Run sheet costs no extra request beyond the run itself.
 */
function CollectionStatesSection({ reportId }: { reportId: string }) {
  const report = useResource(
    (signal) => dashboardApi.report(reportId, signal),
    [reportId],
  );

  if (report.loading) {
    return (
      <Section title="当前采集指针" description="正在读取报告保存的采集指针。">
        <LoadingPanel label="正在读取采集指针" />
      </Section>
    );
  }
  if (report.error) {
    return (
      <Section
        title="当前采集指针"
        description="读取报告保存的采集指针失败。"
      >
        <ErrorPanel message={report.error.message} onRetry={report.refresh} />
      </Section>
    );
  }
  const states = report.data?.latest_collection_states ?? [];
  if (!states.length) {
    return null;
  }
  return (
    <Section
      title="当前采集指针"
      description="这些指向最新采集版本，仅用于说明后续变化；不替换报告实际引用的证据版本。"
    >
      <ul className="divide-y divide-border">
        {states.map((state) => (
          <li
            key={`${state.source_id}-${state.dedupe_key}`}
            className="py-3 text-xs first:pt-0 last:pb-0"
          >
            <p className="font-medium">
              {state.source_id} / {state.dedupe_key}
            </p>
            <p className="mt-1 text-muted-foreground">
              最新 Document{" "}
              <span className="font-code">{shortId(state.latest_document_id)}</span>{" "}
              · 完整度 {statusLabel(state.completeness)} · 最近尝试{" "}
              {formatDateTime(state.last_attempted_at)}
            </p>
          </li>
        ))}
      </ul>
    </Section>
  );
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
    <article className="space-y-5">
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold">{data.task_id}</h3>
          <StatusBadge status={data.status} />
          {data.retry_of_run_id ? (
            <Badge variant="outline">重试 Run</Badge>
          ) : null}
        </div>
        <p className="font-code text-xs break-all text-muted-foreground">
          {data.run_id}
        </p>
        <dl className="grid gap-3 text-xs sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">创建</dt>
            <dd className="tabular-nums">{formatDateTime(data.created_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">开始</dt>
            <dd className="tabular-nums">{formatDateTime(data.started_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">结束</dt>
            <dd className="tabular-nums">{formatDateTime(data.finished_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">触发方式</dt>
            <dd>{data.triggered_by ?? "未记录"}</dd>
          </div>
        </dl>
        {data.change_scan ? (
          <p className="rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">研究增量窗口</span>{" "}
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
        ) : null}
        {data.error?.message || data.error?.code ? (
          <div className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">
            <div className="flex items-center gap-2 font-medium">
              <CircleAlert aria-hidden="true" className="size-4" />
              安全错误
            </div>
            <p className="mt-1">
              {data.error.message ?? data.error.code}
            </p>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
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
        </div>
      </section>

      <Separator />

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">步骤</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            只显示服务端去敏后的定位、状态和安全错误。
          </p>
        </div>
        {data.steps.length ? (
          <div className="space-y-2">
            {data.steps.map((step) => (
              <div
                key={step.step_id}
                className="rounded-lg border border-border px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-code text-muted-foreground">
                    #{step.position}
                  </span>
                  <span className="font-medium">{step.type}</span>
                  <StatusBadge status={step.status} />
                  <span className="ml-auto text-muted-foreground">
                    {stepUsageLine(step.usage) ?? "用量未记录"}
                  </span>
                </div>
                <p className="mt-1 font-code text-xs text-muted-foreground">
                  {step.step_id} · {formatDateTime(step.created_at)}
                </p>
                {step.error?.message || step.error?.code ? (
                  <p className="mt-1 text-xs text-destructive">
                    {step.error.message ?? step.error.code}
                  </p>
                ) : null}
                {step.output !== null && step.output !== undefined ? (
                  <details className="mt-1 text-xs text-muted-foreground">
                    <summary className="min-h-11 cursor-pointer py-3 font-medium text-foreground sm:min-h-0 sm:py-0">
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
      </section>

      <Separator />

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">模型调用</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            显示实际 Provider、模型、耗时、用量、供应商提供的费用与回退原因。
          </p>
        </div>
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
                <TableRow
                  key={`${call.step_id}-${call.attempt_number}-${index}`}
                  className={
                    call.status === "failed"
                      ? "bg-destructive/5 hover:bg-destructive/10"
                      : undefined
                  }
                >
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
                      <span className="mt-1 block text-warning-text">
                        回退：{call.fallback_reason}
                      </span>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={call.status} />
                  </TableCell>
                  <TableCell className="text-xs tabular-nums">
                    {formatDuration(call.duration_ms)}
                  </TableCell>
                  <TableCell className="text-xs tabular-nums">
                    {formatNumber(call.total_tokens)}
                    <span className="block text-muted-foreground">
                      {formatNumber(call.prompt_tokens)} +{" "}
                      {formatNumber(call.completion_tokens)}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs tabular-nums">
                    {formatCost(call.cost_usd)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyPanel title="没有模型调用记录" />
        )}
      </section>

      <Separator />

      <UsageSummarySection
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

      {data.report ? (
        <CollectionStatesSection reportId={data.report.report_id} />
      ) : null}
    </article>
  );
}
