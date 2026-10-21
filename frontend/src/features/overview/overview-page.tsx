import {
  Activity,
  ArrowRight,
  BookOpenText,
  CircleAlert,
  Database,
  Layers3,
} from "lucide-react";
import { Link } from "react-router-dom";
import { getOverview } from "@/api/client";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatNumber, shortId } from "@/lib/format";
import { gapLabel, statusLabel } from "@/lib/labels";
import { UsageModels, UsageStats } from "@/components/usage-summary";

export function OverviewPage() {
  const overview = useResource(
    (signal) => dashboardApi.sources(signal),
    [],
  );
  const dashboard = useResource(
    (signal) => getOverview(signal),
    [],
  );

  if (dashboard.loading) {
    return (
      <div className="space-y-6">
        <PageHeader
          eyebrow="Overview"
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
        <PageHeader eyebrow="Overview" title="运行概览" />
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
  const statusCards = [
    {
      label: "活跃 Run",
      value: data.runs.active_count,
      detail: `${data.runs.failed_count} 个失败 Run`,
      icon: Activity,
      tone: data.runs.failed_count ? "warning" : "default",
    },
    {
      label: "待处理阶段",
      value: healthCount,
      detail: `${data.processing_state.failed} 个失败`,
      icon: Layers3,
      tone: data.processing_state.failed ? "warning" : "default",
    },
    {
      label: "已配置来源",
      value: data.collection.configured_sources,
      detail: `${data.collection.partial_sources.length} 个需关注`,
      icon: Database,
      tone: data.collection.partial_sources.length ? "warning" : "default",
    },
    {
      label: "已保存报告",
      value: data.reports.length,
      detail: "最近显示 5 份",
      icon: BookOpenText,
      tone: "default",
    },
  ] as const;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Overview"
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

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {statusCards.map(({ label, value, detail, icon: Icon, tone }) => (
          <Card key={label} size="sm">
            <CardContent className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="font-editorial mt-1 text-3xl font-semibold">
                  {value}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
              </div>
              <div
                className={
                  tone === "warning"
                    ? "grid size-9 place-items-center rounded-lg bg-amber-50 text-amber-800"
                    : "grid size-9 place-items-center rounded-lg bg-muted text-muted-foreground"
                }
              >
                <Icon aria-hidden="true" className="size-4" />
              </div>
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>研究覆盖</CardTitle>
            <CardDescription>
              最近一份成功报告的证据包；未扫描候选与来源缺口保持未知。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.research.latest ? (
              <>
                <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <dt className="text-muted-foreground">候选发现 / 筛选</dt>
                    <dd className="mt-0.5">
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
                    <dd className="mt-0.5">
                      {data.research.latest.selected_count} 组 /{" "}
                      {formatNumber(
                        data.research.latest.origin_group_count,
                        "未知",
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">未扫描候选</dt>
                    <dd className="mt-0.5">
                      {formatNumber(
                        data.research.latest.unexamined_count,
                        "未知",
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">补证输入</dt>
                    <dd className="mt-0.5">
                      {data.research.latest.research_input_count} 条
                    </dd>
                  </div>
                </dl>
                {data.research.latest.retrieval_truncated ? (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950">
                    本次检索达到预算并截断；未扫描候选数量未知时仍显示未知。
                  </p>
                ) : null}
                {data.research.latest.gaps.length ? (
                  <ul className="flex flex-wrap gap-2 text-xs">
                    {data.research.latest.gaps.map((gap) => (
                      <li
                        key={gap}
                        className="rounded-lg bg-amber-50 px-2 py-1 text-amber-950"
                      >
                        {gapLabel(gap)}
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
              </>
            ) : (
              <EmptyPanel
                title="尚无成功研究报告"
                description="生成报告后，这里显示候选扫描、独立原件与缺口。"
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>近期模型用量</CardTitle>
            <CardDescription>
              最近 {data.research.usage.run_count} 个 Run 已保存的模型调用；未提供费用的调用保持未知。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <UsageStats usage={data.research.usage} />
            <UsageModels usage={data.research.usage} />
          </CardContent>
        </Card>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(18rem,0.7fr)]">
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>近期运行</CardTitle>
              <CardDescription>
                失败、停止和新触发的 Run 保留安全错误定位。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {data.runs.recent.length ? (
                <div className="divide-y divide-border">
                  {data.runs.recent.map((run) => (
                    <Link
                      key={run.run_id}
                      to={`/runs?run=${run.run_id}`}
                      className="flex items-start justify-between gap-3 py-3 first:pt-0 last:pb-0"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium">{run.task_id}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {formatDateTime(run.created_at)} ·{" "}
                          {run.step_count ?? 0} 个步骤
                        </p>
                        {run.error?.message ? (
                          <p className="mt-1 line-clamp-2 text-xs text-destructive">
                            {run.error.message}
                          </p>
                        ) : null}
                      </div>
                      <StatusBadge status={run.status} />
                    </Link>
                  ))}
                </div>
              ) : (
                <EmptyPanel title="尚未运行任何任务" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>最新 Document</CardTitle>
              <CardDescription>
                采集成功不代表已完成解析或向量索引。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {data.recent_documents.length ? (
                <div className="grid gap-3">
                  {data.recent_documents.map((document) => {
                    const parsed = document.parse_state?.status;
                    const indexed = document.embedding_state?.status;
                    return (
                      <Link
                        key={document.id}
                        to={`/intelligence?document=${document.id}`}
                        className="rounded-lg border border-border p-3 transition-colors hover:bg-muted/40"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="min-w-0 flex-1 truncate font-medium">
                            {document.title}
                          </p>
                          <Badge variant="outline">v{document.version}</Badge>
                          <StatusBadge status={document.completeness} />
                        </div>
                        <p className="mt-2 text-xs text-muted-foreground">
                          {document.source_id} · 采集{" "}
                          {formatDateTime(document.collected_at)} · 解析{" "}
                          {statusLabel(parsed ?? "missing")} · 索引{" "}
                          {statusLabel(indexed ?? "pending")}
                        </p>
                      </Link>
                    );
                  })}
                </div>
              ) : (
                <EmptyPanel title="仓库中还没有 Document" />
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>来源状态</CardTitle>
              <CardDescription>
                显示来源请求、新鲜度和后处理积压。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {overview.loading ? (
                <LoadingPanel label="正在读取来源" />
              ) : overview.error ? (
                <ErrorPanel
                  message={overview.error.message}
                  onRetry={overview.refresh}
                />
              ) : overview.data?.items.length ? (
                <div className="space-y-3">
                  {overview.data.items.slice(0, 8).map((source) => (
                    <div
                      key={source.source_id}
                      className="rounded-lg border border-border p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="min-w-0 flex-1 truncate font-medium">
                          {source.name}
                        </p>
                        <StatusBadge status={source.collection.status} />
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge variant="outline">
                          新鲜度 {statusLabel(source.freshness.status)}
                        </Badge>
                        <span>
                          尝试 {formatDateTime(source.collection.latest_attempt_at)}
                        </span>
                      </div>
                      {source.collection.processing_backlog ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          处理积压{" "}
                          {source.collection.processing_backlog.pending} · 失败{" "}
                          {source.collection.processing_backlog.failed}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyPanel title="没有可展示的来源" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>近期报告</CardTitle>
            </CardHeader>
            <CardContent>
              {data.reports.length ? (
                <div className="space-y-2">
                  {data.reports.map((report) => (
                    <Link
                      key={report.report_id}
                      to={`/research?report=${report.report_id}`}
                      className="block rounded-lg p-2 transition-colors hover:bg-muted"
                    >
                      <p className="line-clamp-2 font-medium">{report.title}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatDateTime(report.created_at)} ·{" "}
                        {shortId(report.run_id)}
                      </p>
                    </Link>
                  ))}
                </div>
              ) : (
                <EmptyPanel title="尚无成功报告" />
              )}
            </CardContent>
          </Card>

          {data.collection.partial_sources.length ? (
            <Card className="border-amber-200">
              <CardHeader>
                <div className="flex items-center gap-2">
                  <CircleAlert
                    aria-hidden="true"
                    className="size-4 text-amber-700"
                  />
                  <CardTitle>需关注的来源</CardTitle>
                </div>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                {data.collection.partial_sources.map((source) => (
                  <p key={source.source_id}>
                    {source.name}：{statusLabel(source.collection.status)}
                    {source.collection.error?.message
                      ? ` · ${source.collection.error.message}`
                      : ""}
                  </p>
                ))}
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
