import type { ReactNode } from "react";
import type { UsageSummary } from "@/api/schemas";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatCost, formatDuration, formatNumber } from "@/lib/format";

/**
 * Deterministic usage from stored model calls. Missing provider-reported cost
 * renders as 未知 with an explicit count, never as a zero amount.
 */
export function UsageStats({ usage }: { usage: UsageSummary }) {
  return (
    <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <dt className="text-muted-foreground">模型调用</dt>
        <dd className="mt-0.5">
          {usage.call_count} 次
          <span className="block text-muted-foreground">
            成功 {usage.succeeded_calls} · 失败 {usage.failed_calls}
            {usage.fallback_calls ? ` · 回退 ${usage.fallback_calls}` : ""}
          </span>
        </dd>
      </div>
      <div>
        <dt className="text-muted-foreground">Tokens</dt>
        <dd className="mt-0.5">
          {formatNumber(usage.total_tokens, "未知")}
          <span className="block text-muted-foreground">
            {formatNumber(usage.prompt_tokens, "未知")} +{" "}
            {formatNumber(usage.completion_tokens, "未知")}
          </span>
        </dd>
      </div>
      <div>
        <dt className="text-muted-foreground">模型耗时</dt>
        <dd className="mt-0.5">{formatDuration(usage.duration_ms)}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">费用（供应商提供）</dt>
        <dd className="mt-0.5">
          {formatCost(usage.cost_usd)}
          {usage.cost_unknown_calls ? (
            <span className="block text-muted-foreground">
              {usage.cost_unknown_calls} 次调用未提供费用
            </span>
          ) : null}
        </dd>
      </div>
    </dl>
  );
}

export function UsageModels({ usage }: { usage: UsageSummary }) {
  const models = usage.models ?? [];
  if (!models.length) {
    return null;
  }
  return (
    <ul className="space-y-1 text-xs text-muted-foreground">
      {models.map((model) => (
        <li key={`${model.provider ?? "-"}::${model.model_name ?? "-"}`}>
          {model.provider ?? "未记录 Provider"} ·{" "}
          {model.model_name ?? "未记录模型"} · {model.calls} 次 · Tokens{" "}
          {formatNumber(model.total_tokens, "未知")} · 费用{" "}
          {formatCost(model.cost_usd)}
          {model.cost_unknown_calls
            ? `（${model.cost_unknown_calls} 次未提供费用）`
            : ""}
        </li>
      ))}
      {usage.models_truncated ? (
        <li>仅显示调用次数最多的 10 个模型。</li>
      ) : null}
    </ul>
  );
}

export function UsageSummaryCard({
  usage,
  title = "模型用量",
  description = "来自已保存的模型调用记录；未提供费用的调用保持未知，不按 0 计算。",
  footer,
}: {
  usage: UsageSummary;
  title?: string;
  description?: string;
  footer?: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <UsageStats usage={usage} />
        <UsageModels usage={usage} />
        {footer}
      </CardContent>
    </Card>
  );
}
