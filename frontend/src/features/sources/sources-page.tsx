import { dashboardApi } from "@/api/dashboard";
import type { SafeError, SourceSummary, SourcesResponse } from "@/api/schemas";
import {
  EmptyPanel,
  ErrorPanel,
  LoadingPanel,
} from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime } from "@/lib/format";
import { statusLabel, statusTone, trustTierLabel } from "@/lib/labels";

function safeErrorText(error: SafeError): string | null {
  if (!error) {
    return null;
  }
  if (error.message) {
    return error.code ? `[${error.code}] ${error.message}` : error.message;
  }
  return error.code ?? null;
}

function rowErrorText(source: SourceSummary): string | null {
  const messages = [
    safeErrorText(source.collection.error),
    safeErrorText(source.collection.last_attempt_error),
  ].filter((value): value is string => Boolean(value));
  return [...new Set(messages)].join("；") || null;
}

function freshnessTitle(
  freshness: SourceSummary["freshness"],
): string | undefined {
  const parts = [
    freshness.latest_disclosure_at
      ? `最新披露 ${formatDateTime(freshness.latest_disclosure_at)}`
      : null,
    freshness.latest_collected_at
      ? `最近采集 ${formatDateTime(freshness.latest_collected_at)}`
      : null,
  ].filter((value): value is string => Boolean(value));
  return parts.length ? parts.join("；") : undefined;
}

function LifecycleBadges({ source }: { source: SourceSummary }) {
  return (
    <span className="flex flex-wrap gap-1">
      {source.enabled === false ? (
        <Badge variant="secondary">已禁用</Badge>
      ) : source.enabled === true ? (
        <Badge variant="outline">已启用</Badge>
      ) : (
        <Badge variant="outline" title="接口未返回启用状态">
          启用状态未知
        </Badge>
      )}
      {!source.configured ? <Badge variant="destructive">未配置</Badge> : null}
      {!source.persisted ? <Badge variant="outline">无历史数据</Badge> : null}
    </span>
  );
}

function SourcesTable({ data }: { data: SourcesResponse }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>来源</TableHead>
          <TableHead>Connector</TableHead>
          <TableHead>发布者</TableHead>
          <TableHead>信任级别</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>采集状态</TableHead>
          <TableHead>新鲜度</TableHead>
          <TableHead>最近尝试</TableHead>
          <TableHead>积压</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.items.map((source) => {
          const errorText = rowErrorText(source);
          const backlog = source.collection.processing_backlog;
          return (
            <TableRow key={source.source_id}>
              <TableCell className="max-w-56">
                <p className="truncate font-medium" title={source.name}>
                  {source.name}
                </p>
                <p
                  className="truncate font-code text-xs text-muted-foreground"
                  title={source.source_id}
                >
                  {source.source_id}
                </p>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {source.connector ?? "未记录"}
              </TableCell>
              <TableCell className="max-w-48 text-xs text-muted-foreground">
                <span
                  className="block truncate"
                  title={source.publisher ?? undefined}
                >
                  {source.publisher ?? "未记录"}
                </span>
              </TableCell>
              <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                {trustTierLabel(source.trust_tier)}
              </TableCell>
              <TableCell>
                <LifecycleBadges source={source} />
              </TableCell>
              <TableCell>
                <StatusBadge status={source.collection.status} />
                {errorText ? (
                  <p
                    className="mt-1 line-clamp-2 max-w-72 text-xs text-destructive"
                    title={errorText}
                  >
                    {errorText}
                  </p>
                ) : null}
              </TableCell>
              <TableCell>
                <Badge
                  variant={statusTone(source.freshness.status)}
                  title={freshnessTitle(source.freshness)}
                >
                  {statusLabel(source.freshness.status)}
                </Badge>
              </TableCell>
              <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                {formatDateTime(source.collection.latest_attempt_at)}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {backlog &&
                (backlog.pending > 0 || backlog.failed > 0)
                  ? `待处理 ${backlog.pending} · 失败 ${backlog.failed}`
                  : "无积压"}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function SourcesView({ data }: { data: SourcesResponse }) {
  const configMessage =
    data.configuration_error?.message ?? data.configuration_error?.code ?? null;

  return (
    <>
      {configMessage ? (
        <ErrorPanel
          title="sources.yaml 配置未能读取"
          message={configMessage}
        />
      ) : null}
      {data.items.length ? (
        <div className="space-y-3">
          <SourcesTable data={data} />
          {data.has_more ? (
            <p className="text-xs text-muted-foreground">
              来源超过单页 {data.limit} 个的限制，当前仅显示前{" "}
              {data.items.length} 个。
            </p>
          ) : null}
        </div>
      ) : (
        <EmptyPanel
          title="尚未接入任何采集源"
          description="在 config/sources.yaml 中配置来源并完成采集后，这里会显示全部来源的状态。"
        />
      )}
    </>
  );
}

export function SourcesPage() {
  const sources = useResource((signal) => dashboardApi.sources(signal), []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="采集源"
        description="系统当前接入的数据来源与采集状态。"
      />
      {sources.loading ? (
        <LoadingPanel label="正在读取采集源" />
      ) : sources.error || !sources.data ? (
        <ErrorPanel
          message={sources.error?.message ?? "无法读取采集源"}
          onRetry={sources.refresh}
        />
      ) : (
        <SourcesView data={sources.data} />
      )}
    </div>
  );
}
