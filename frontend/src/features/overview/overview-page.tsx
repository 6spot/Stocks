import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { getOverview } from "@/api/client";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { Section } from "@/components/section";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatNumber, shortId } from "@/lib/format";
import { gapLabel, statusLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { UsageModels, UsageStats } from "@/components/usage-summary";

export function OverviewPage() {
  const overview = useResource((signal) => dashboardApi.sources(signal), []);
  const dashboard = useResource((signal) => getOverview(signal), []);

  if (dashboard.loading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="运行概览"
          description="采集、处理与研究状态的统一入口。"
        />
        <LoadingPanel />
      </div>
    );
  }
  if (dashboard.error || !dashboard.data) {
    return (
      <div className="space-y-6">
        <PageHeader title="运行概览" />
        <ErrorPanel
          message={dashboard.error?.message ?? "概览为空"}
          onRetry={dashboard.refresh}
        />
      </div>
    );
  }

  const data = dashboard.data;
  const healthCount =
    data.processing_state.pending +
    data.processing_state.running +
    data.processing_state.failed;
  const stats = [
    {
      label: "活跃 Run",
      value: data.runs.active_count,
      detail: `${data.runs.failed_count} 个失败 Run`,
      warning: Boolean(data.runs.failed_count),
    },
    {
      label: "待处理阶段",
      value: healthCount,
      detail: `${data.processing_state.failed} 个失败`,
      warning: Boolean(data.processing_state.failed),
    },
    {
      label: "已配置来源",
      value: data.collection.configured_sources,
      detail: `${data.collection.partial_sources.length} 个需关注`,
      warning: Boolean(data.collection.partial_sources.length),
    },
    {
      label: "已保存报告",
      value: data.reports.length,
      detail: "最近显示 5 份",
      warning: false,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="运行概览"
        description="从采集中断、处理积压到最新研究，先看需要 Owner 判断的异常。"
        actions={
          <Link
            to="/intelligence"
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            搜索情报
            <ArrowRight aria-hidden="true" />
          </Link>
        }
      />

      {data.configuration_errors.sources?.message ||
      data.configuration_errors.tasks?.message ? (
        <ErrorPanel
          title="配置文件存在错误"
          message={
            [
              data.configuration_errors.sources?.message,
              data.configuration_errors.tasks?.message,
            ]
              .filter(Boolean)
              .join("；") || "来源或任务配置未能读取"
          }
        />
      ) : null}

      <section
        aria-label="关键统计"
        className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4 lg:divide-x lg:divide-border"
      >
        {stats.map(({ label, value, detail, warning }) => (
          <div key={label} className="min-w-0 lg:pl-4 first:lg:pl-0">
            <p
              className="text-2xl font-semibold tabular-nums"
              aria-label={`${label} ${value}`}
            >
              {value}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {label} ·{" "}
              <span
                className={cn(
                  warning && "text-warning-text",
                )}
              >
                {detail}
              </span>
            </p>
          </div>
        ))}
      </section>

      <Section
        title="研究覆盖"
        description="最近一份成功报告的证据包；未扫描候选与来源缺口保持未知。"
      >
        {data.research.latest ? (
          <div className="space-y-4">
            <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-muted-foreground">候选发现 / 筛选</dt>
                <dd className="mt-0.5 tabular-nums">
                  {formatNumber(
                    data.research.latest.candidates_discovered,
                    "未知",
                  )}{" "}
                  /{" "}
                  {formatNumber(
                    data.research.latest.candidates_screened,
                    "未知",
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">入选证据 / 独立原件</dt>
                <dd className="mt-0.5 tabular-nums">
                  {data.research.latest.selected_count} 组 /{" "}
                  {formatNumber(
                    data.research.latest.origin_group_count,
                    "未知",
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">未扫描候选</dt>
                <dd className="mt-0.5 tabular-nums">
                  {formatNumber(
                    data.research.latest.unexamined_count,
                    "未知",
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">补证输入</dt>
                <dd className="mt-0.5 tabular-nums">
                  {data.research.latest.research_input_count} 条
                </dd>
              </div>
            </dl>
            {data.research.latest.retrieval_truncated ? (
              <Alert variant="warning" className="mt-3">
                <AlertTitle>本次检索达到预算并截断</AlertTitle>
                <AlertDescription>
                  未扫描候选数量未知时仍显示未知。
                </AlertDescription>
              </Alert>
            ) : null}
            {data.research.latest.gaps.length ? (
              <ul className="flex flex-wrap gap-2 text-xs">
                {data.research.latest.gaps.map((gap) => (
                  <li key={gap}>
                    <Badge variant="warning">{gapLabel(gap)}</Badge>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                证据包未记录来源或索引缺口。
              </p>
            )}
            <Link
              to={`/research?report=${data.research.latest.report_id}`}
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              打开 {data.research.latest.title}
            </Link>
          </div>
        ) : (
          <EmptyPanel
            title="尚无成功研究报告"
            description="生成报告后，这里显示候选扫描、独立原件与缺口。"
          />
        )}
      </Section>

      <Section
        title="近期模型用量"
        description={`最近 ${data.research.usage.run_count} 个 Run 已保存的模型调用；未提供费用的调用保持未知。`}
      >
        <div className="space-y-3">
          <UsageStats usage={data.research.usage} />
          <UsageModels usage={data.research.usage} />
        </div>
      </Section>

      <Section
        title="近期运行"
        description="失败、停止和新触发的 Run 保留安全错误定位。"
        contentClassName="space-y-0"
      >
        {data.runs.recent.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>任务</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="w-1/3">步骤</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.runs.recent.map((run) => (
                <TableRow key={run.run_id}>
                  <TableCell className="max-w-64">
                    <Link
                      to={`/runs?run=${run.run_id}`}
                      className="truncate font-medium text-foreground hover:text-primary hover:underline"
                    >
                      {run.task_id}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={run.status} />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(run.created_at)}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {run.step_count ?? 0} 个步骤
                    {run.error?.message ? (
                      <span className="mt-1 line-clamp-2 block text-destructive">
                        {run.error.message}
                      </span>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyPanel title="尚未运行任何任务" />
        )}
      </Section>

      <Section
        title="最新 Document"
        description="采集成功不代表已完成解析或向量索引。"
        contentClassName="space-y-0"
      >
        {data.recent_documents.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>标题</TableHead>
                <TableHead>版本</TableHead>
                <TableHead>完整度</TableHead>
                <TableHead>采集时间</TableHead>
                <TableHead>解析 / 索引</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.recent_documents.map((document) => {
                const parsed = document.parse_state?.status;
                const indexed = document.embedding_state?.status;
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
                    <TableCell>
                      <Badge variant="outline">v{document.version}</Badge>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={document.completeness} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatDateTime(document.collected_at)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      解析 {statusLabel(parsed ?? "missing")} · 索引{" "}
                      {statusLabel(indexed ?? "pending")}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        ) : (
          <EmptyPanel title="仓库中还没有 Document" />
        )}
      </Section>

      <Section
        title="来源状态"
        description="显示来源请求、新鲜度和后处理积压。"
        contentClassName="space-y-0"
      >
        {overview.loading ? (
          <LoadingPanel label="正在读取来源" />
        ) : overview.error ? (
          <ErrorPanel
            message={overview.error.message}
            onRetry={overview.refresh}
          />
        ) : overview.data?.items.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>来源</TableHead>
                <TableHead>采集状态</TableHead>
                <TableHead>新鲜度</TableHead>
                <TableHead>最近尝试</TableHead>
                <TableHead>积压</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.data.items.slice(0, 8).map((source) => (
                <TableRow key={source.source_id}>
                  <TableCell className="max-w-56">
                    <p className="truncate font-medium" title={source.name}>
                      {source.name}
                    </p>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={source.collection.status} />
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">
                      {statusLabel(source.freshness.status)}
                    </Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(source.collection.latest_attempt_at)}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {source.collection.processing_backlog
                      ? `待处理 ${source.collection.processing_backlog.pending} · 失败 ${source.collection.processing_backlog.failed}`
                      : "无积压"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyPanel title="没有可展示的来源" />
        )}
      </Section>

      <Section title="近期报告" contentClassName="space-y-0">
        {data.reports.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>标题</TableHead>
                <TableHead>生成时间</TableHead>
                <TableHead>Run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.reports.map((report) => (
                <TableRow key={report.report_id}>
                  <TableCell className="max-w-80">
                    <Link
                      to={`/research?report=${report.report_id}`}
                      className="line-clamp-2 font-medium text-foreground hover:text-primary hover:underline"
                    >
                      {report.title}
                    </Link>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(report.created_at)}
                  </TableCell>
                  <TableCell className="font-code text-xs text-muted-foreground">
                    {shortId(report.run_id)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyPanel title="尚无成功报告" />
        )}
      </Section>

      {data.collection.partial_sources.length ? (
        <Section
          title="需关注的来源"
          description="这些来源采集未完成；缺口会进入研究覆盖统计。"
        >
          <ul className="grid gap-2 text-xs sm:grid-cols-2">
            {data.collection.partial_sources.map((source) => (
              <li key={source.source_id}>
                {source.name}：{statusLabel(source.collection.status)}
                {source.collection.error?.message
                  ? ` · ${source.collection.error.message}`
                  : ""}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
