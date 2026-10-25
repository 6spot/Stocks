import { ArrowRight, CircleAlert, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { getOverview } from "@/api/client";
import type { Overview } from "@/api/schemas";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { Section } from "@/components/section";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatNumber } from "@/lib/format";
import { statusLabel } from "@/lib/labels";

const chartConfig = {
  documents: {
    label: "文档",
    color: "var(--chart-documents)",
  },
  facts: {
    label: "Fact",
    color: "var(--chart-facts)",
  },
} satisfies ChartConfig;

function dayLabel(date: string): string {
  // "2026-10-24" → "10-24": the year is already implied by the range.
  return date.slice(5);
}

/**
 * Recharts animates with requestAnimationFrame, which the global
 * prefers-reduced-motion CSS cannot reach. Bars therefore opt out of their
 * growth animation when the user asked for less motion.
 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const listener = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []);
  return reduced;
}

function StatStrip({ library }: { library: Overview["library"] }) {
  const stats = [
    { label: "文档", value: library.documents },
    { label: "Fact", value: library.facts },
    { label: "报告", value: library.reports },
    { label: "来源", value: library.sources },
  ];
  const span =
    library.earliest_collected_at && library.latest_collected_at
      ? `${library.earliest_collected_at.slice(0, 10)} ~ ${library.latest_collected_at.slice(0, 10)}`
      : "尚无采集记录";
  return (
    <section aria-label="情报存量" className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4 lg:divide-x lg:divide-border">
        {stats.map(({ label, value }) => (
          <div key={label} className="min-w-0 lg:pl-4 first:lg:pl-0">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums">
              {formatNumber(value)}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        本周新增 文档 {formatNumber(library.documents_this_week)} · Fact{" "}
        {formatNumber(library.facts_this_week)} · 时间跨度 {span}
      </p>
    </section>
  );
}

function InflowChart({ library }: { library: Overview["library"] }) {
  const reducedMotion = usePrefersReducedMotion();
  const data = library.daily.map((item) => ({
    ...item,
    label: dayLabel(item.date),
  }));
  const empty = data.every((item) => !item.documents && !item.facts);
  return (
    <Section
      title="数据流入"
      description="最近 14 天每日新增文档与 Fact；无数据日期补 0。"
    >
      {empty ? (
        <EmptyPanel
          title="最近 14 天没有新增数据"
          description="采集或抽取产生新内容后，这里显示每日流入。"
        />
      ) : (
        <ChartContainer
          config={chartConfig}
          className="h-56 w-full"
          role="img"
          aria-label="最近 14 天文档与 Fact 新增柱状图"
        >
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0 }}>
            <CartesianGrid vertical={false} strokeDasharray="3 3" />
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={false}
              tickMargin={8}
              minTickGap={8}
            />
            <YAxis
              width={32}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
              tickMargin={4}
            />
            <ChartTooltip
              cursor={false}
              content={
                <ChartTooltipContent
                  labelFormatter={(_, entries) => {
                    const date = entries[0]?.payload?.date;
                    return typeof date === "string" ? date : "";
                  }}
                />
              }
            />
            <ChartLegend content={<ChartLegendContent />} />
            <Bar
              dataKey="documents"
              fill="var(--color-documents)"
              radius={[4, 4, 0, 0]}
              isAnimationActive={!reducedMotion}
            />
            <Bar
              dataKey="facts"
              fill="var(--color-facts)"
              radius={[4, 4, 0, 0]}
              isAnimationActive={!reducedMotion}
            />
          </BarChart>
        </ChartContainer>
      )}
    </Section>
  );
}

function SourceTable({ library }: { library: Overview["library"] }) {
  return (
    <Section
      title="来源分布"
      description="每个来源贡献的文档与 Fact；新鲜度按该来源配置的预期披露间隔判断。"
      contentClassName="space-y-0"
    >
      {library.by_source.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>来源</TableHead>
              <TableHead className="text-right">文档</TableHead>
              <TableHead className="text-right">Fact</TableHead>
              <TableHead>最近成功</TableHead>
              <TableHead>新鲜度</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {library.by_source.map((source) => (
              <TableRow key={source.source_id}>
                <TableCell className="max-w-56">
                  <Link
                    to="/sources"
                    className="block truncate font-medium text-foreground hover:text-primary hover:underline"
                    title={source.name}
                  >
                    {source.name}
                  </Link>
                  <span className="truncate font-code text-xs text-muted-foreground">
                    {source.source_id}
                  </span>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(source.documents)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(source.facts)}
                </TableCell>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                  {formatDateTime(source.last_success_at)}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={
                      source.freshness === "fresh"
                        ? "success"
                        : source.freshness === "stale"
                          ? "warning"
                          : "outline"
                    }
                    title={
                      source.expected_freshness
                        ? `预期 ${source.expected_freshness}`
                        : "未配置预期新鲜度"
                    }
                  >
                    {statusLabel(source.freshness)}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <EmptyPanel
          title="还没有来源数据"
          description="采集成功入库后，这里按来源显示文档与 Fact 数量。"
        />
      )}
    </Section>
  );
}

function AttentionSection({ attention }: { attention: Overview["attention"] }) {
  return (
    <Section
      title="需要处理"
      description="确定性检查得到的异常；没有异常时不显示告警行。"
    >
      {attention.length ? (
        <ul className="divide-y divide-border">
          {attention.map((item, index) => {
            const Icon =
              item.severity === "error" ? CircleAlert : TriangleAlert;
            return (
              <li
                key={`${item.kind}-${item.source_id ?? item.task_id ?? index}`}
                className="flex flex-wrap items-center justify-between gap-3 py-2 first:pt-0 last:pb-0"
              >
                <span className="flex min-w-0 items-start gap-2 text-sm">
                  <Icon
                    aria-hidden="true"
                    className={
                      item.severity === "error"
                        ? "mt-0.5 size-4 shrink-0 text-destructive"
                        : "mt-0.5 size-4 shrink-0 text-warning-text"
                    }
                  />
                  <span className="min-w-0">{item.message}</span>
                </span>
                <Link
                  to={item.link}
                  aria-label={`查看：${item.message}`}
                  className="text-xs font-medium text-primary underline-offset-4 hover:underline"
                >
                  查看
                </Link>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">无阻塞</p>
      )}
    </Section>
  );
}

function ReportTable({ reports }: { reports: Overview["reports"] }) {
  return (
    <Section
      title="最新报告"
      description="研究产出的报告；独立原件与缺口来自保存的证据包。"
      contentClassName="space-y-0"
    >
      {reports.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>标题</TableHead>
              <TableHead>生成时间</TableHead>
              <TableHead className="text-right">独立原件</TableHead>
              <TableHead className="text-right">缺口</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {reports.map((report) => (
              <TableRow key={report.report_id}>
                <TableCell className="max-w-80">
                  <Link
                    to={`/research/${encodeURIComponent(report.report_id)}`}
                    className="line-clamp-2 font-medium text-foreground hover:text-primary hover:underline"
                  >
                    {report.title}
                  </Link>
                </TableCell>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                  {formatDateTime(report.created_at)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(report.origin_group_count, "未知")}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(report.gap_count, "未知")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <EmptyPanel title="尚无研究报告" />
      )}
    </Section>
  );
}

function RecentDocumentTable({
  documents,
}: {
  documents: Overview["recent_documents"];
}) {
  return (
    <Section
      title="最近入库"
      description="最近采集的文档；采集成功不代表已完成解析或向量索引。"
      contentClassName="space-y-0"
    >
      {documents.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>标题</TableHead>
              <TableHead>来源</TableHead>
              <TableHead>采集时间</TableHead>
              <TableHead>处理状态</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {documents.map((document) => {
              const stages = document.processing_stages ?? [];
              const failed = stages.filter(
                (stage) => stage.status === "failed",
              ).length;
              const pending = stages.filter(
                (stage) =>
                  stage.status === "pending" || stage.status === "running",
              ).length;
              const completed = stages.filter(
                (stage) => stage.status === "completed",
              ).length;
              const skipped = stages.filter(
                (stage) => stage.status === "skipped",
              ).length;
              return (
                <TableRow key={document.id}>
                  <TableCell className="max-w-72">
                    <Link
                      to={`/intelligence?document=${document.id}`}
                      className="block truncate font-medium text-foreground hover:text-primary hover:underline"
                      title={document.title}
                    >
                      {document.title}
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-40 truncate text-xs text-muted-foreground">
                    {document.source_id}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(document.collected_at)}
                  </TableCell>
                  <TableCell>
                    {failed ? (
                      <StatusBadge status="failed" />
                    ) : pending ? (
                      <StatusBadge status="pending" />
                    ) : completed ? (
                      <StatusBadge status="completed" />
                    ) : skipped ? (
                      <StatusBadge status="skipped" />
                    ) : (
                      <Badge variant="outline">未创建</Badge>
                    )}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {completed}/{stages.length || 4}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : (
        <EmptyPanel title="仓库中还没有文档" />
      )}
    </Section>
  );
}

export function OverviewPage() {
  const dashboard = useResource((signal) => getOverview(signal), []);

  if (dashboard.loading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="情报概览"
          description="已积累的情报、新鲜度与需要处理的事项。"
        />
        <LoadingPanel />
      </div>
    );
  }
  if (dashboard.error || !dashboard.data) {
    return (
      <div className="space-y-6">
        <PageHeader title="情报概览" />
        <ErrorPanel
          message={dashboard.error?.message ?? "概览为空"}
          onRetry={dashboard.refresh}
        />
      </div>
    );
  }

  const data = dashboard.data;

  return (
    <div className="space-y-8 motion-rise">
      <PageHeader
        title="情报概览"
        description="已积累的情报、来源新鲜度与需要 Owner 判断的事项。"
        actions={
          <Link
            to="/runs"
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            查看运行与积压
            <ArrowRight aria-hidden="true" />
          </Link>
        }
      />

      <StatStrip library={data.library} />
      <InflowChart library={data.library} />
      <SourceTable library={data.library} />
      <AttentionSection attention={data.attention} />
      <ReportTable reports={data.reports} />
      <RecentDocumentTable documents={data.recent_documents} />
    </div>
  );
}
