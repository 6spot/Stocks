import {
  BookOpenText,
  Calendar,
  ChevronRight,
  FileText,
  Layers3,
  ShieldCheck,
} from "lucide-react";
import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { dashboardApi } from "@/api/dashboard";
import type { ReportEvidence } from "@/api/schemas";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { FactList } from "@/components/fact-list";
import {
  MetricComparisonsCard,
  SourceIndependenceCard,
} from "@/components/source-independence";
import { OriginalLinks } from "@/components/original-links";
import { PageHeader } from "@/components/page-header";
import { ReportComparisonCard } from "@/components/report-comparison";
import { StatusBadge } from "@/components/status-badge";
import { UsageSummaryCard } from "@/components/usage-summary";
import type { OriginGroup, ReportListItem } from "@/api/schemas";
import { evidenceCoverageSchema } from "@/api/schemas";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatValue, shortId } from "@/lib/format";
import { gapLabel, statusLabel } from "@/lib/labels";

export function ResearchPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("report");
  const reports = useResource(
    (signal) => dashboardApi.reports(100, signal),
    [],
  );
  // The list is ordered newest first, so this is the most recent report whose
  // Run actually succeeded; failed Runs never replace the last readable one.
  const latestSuccessfulId = reports.data?.items.find(
    (report) => report.run_status === "succeeded",
  )?.report_id;

  useEffect(() => {
    if (
      selectedId ||
      !latestSuccessfulId ||
      !window.matchMedia("(min-width: 80rem)").matches
    ) {
      return;
    }
    const next = new URLSearchParams(params);
    next.set("report", latestSuccessfulId);
    setParams(next, { replace: true });
  }, [params, selectedId, latestSuccessfulId, setParams]);

  function selectReport(reportId: string) {
    const next = new URLSearchParams(params);
    next.set("report", reportId);
    setParams(next, { replace: true });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Research"
        title="研究报告"
        description="统一阅读市场、行业和个股研究；先核对报告实际使用的证据版本，再打开原件。"
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(18rem,0.56fr)_minmax(0,1.44fr)]">
        <section className="space-y-3" aria-label="报告列表">
          {reports.loading ? (
            <LoadingPanel label="正在读取报告列表" />
          ) : reports.error ? (
            <ErrorPanel
              message={reports.error.message}
              onRetry={reports.refresh}
            />
          ) : !reports.data?.items.length ? (
            <EmptyPanel
              title="尚无成功报告"
              description="失败 Run 不会替换最后成功保存的报告。"
            />
          ) : (
            reports.data.items.map((report) => (
              <button
                key={report.report_id}
                type="button"
                aria-pressed={selectedId === report.report_id}
                onClick={() => selectReport(report.report_id)}
                className={
                  selectedId === report.report_id
                    ? "w-full rounded-xl border border-primary/40 bg-card p-3 text-left ring-2 ring-primary/20"
                    : "w-full rounded-xl border border-border bg-card p-3 text-left transition-colors hover:bg-muted/40"
                }
              >
                <div className="flex items-start gap-2">
                  <FileText
                    aria-hidden="true"
                    className="mt-0.5 size-4 shrink-0 text-primary"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="line-clamp-2 font-medium">{report.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatDateTime(report.created_at)}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{report.task_id}</Badge>
                      <StatusBadge status={report.run_status} />
                    </div>
                  </div>
                  <ChevronRight
                    aria-hidden="true"
                    className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                  />
                </div>
              </button>
            ))
          )}
        </section>

        <section className="min-w-0">
          {selectedId ? (
            <ReportReader
              key={selectedId}
              reportId={selectedId}
              reports={
                reports.data?.items.filter(
                  (report) => report.run_status === "succeeded",
                ) ?? []
              }
              onSelectReport={selectReport}
            />
          ) : (
            <EmptyPanel
              title="选择一份报告"
              description="正文、覆盖缺口和引用证据会一起显示。"
              action={
                latestSuccessfulId ? (
                  <button
                    type="button"
                    className="text-sm font-medium text-primary underline-offset-4 hover:underline"
                    onClick={() => selectReport(latestSuccessfulId)}
                  >
                    打开最新报告
                  </button>
                ) : undefined
              }
            />
          )}
        </section>
      </div>
    </div>
  );
}

export function ReportReader({
  reportId,
  reports = [],
  onSelectReport,
}: {
  reportId: string;
  reports?: ReportListItem[];
  onSelectReport?: (reportId: string) => void;
}) {
  const report = useResource(
    (signal) => dashboardApi.report(reportId, signal),
    [reportId],
  );

  if (report.loading) {
    return <LoadingPanel label="正在读取报告" />;
  }
  if (report.error) {
    return <ErrorPanel message={report.error.message} onRetry={report.refresh} />;
  }
  if (!report.data) {
    return <EmptyPanel title="没有报告内容" />;
  }

  const data = report.data;
  const cited = new Set(data.cited_chunk_ids);
  const coverage = evidenceCoverageSchema.safeParse(
    data.evidence_package.coverage,
  );
  const originGroups = coverage.success
    ? coverage.data.origin_groups ?? []
    : [];
  const evidenceGroupCount = coverage.success
    ? coverage.data.evidence_group_count ?? null
    : null;
  const comparisons =
    data.evidence_package.final_metric_comparisons ??
    data.evidence_package.metric_comparisons ??
    [];
  const originById = new Map(
    originGroups.map((group) => [group.origin_id, group]),
  );
  return (
    <article className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <BookOpenText aria-hidden="true" className="size-4 text-primary" />
            <CardTitle className="font-editorial text-2xl leading-tight">
              {data.report.title}
            </CardTitle>
            {data.run && typeof data.run === "object" && "status" in data.run ? (
              <StatusBadge status={String(data.run.status)} />
            ) : null}
          </div>
          <CardDescription>
            {data.report.task_id} · 生成于{" "}
            {formatDateTime(data.report.created_at)}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">研究 as_of</dt>
              <dd className="mt-0.5">
                {formatDateTime(data.evidence_package.as_of)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">证据包创建</dt>
              <dd className="mt-0.5">
                {formatDateTime(data.evidence_package.created_at)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">选定证据</dt>
              <dd className="mt-0.5">
                {data.evidence_package.selected_count} 组
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">独立原件</dt>
              <dd className="mt-0.5">
                {originGroups.length ? `${originGroups.length} 个` : "未知"}
              </dd>
            </div>
          </dl>
          {data.evidence_package.retrieval_truncated ? (
            <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950">
              证据检索达到预算并发生截断；报告未读取所有候选资料。
            </p>
          ) : null}
          <div className="font-editorial rounded-xl border border-border bg-background/80 p-4 text-[1.02rem] leading-8 whitespace-pre-wrap sm:p-6">
            {data.report.content}
          </div>
        </CardContent>
      </Card>

      {data.coverage_gaps.length ? (
        <Card className="border-amber-200">
          <CardHeader>
            <CardTitle>资料缺口与不确定性</CardTitle>
            <CardDescription>
              缺口来自保存时的覆盖统计和当前读取结果，不代表资料在现实中不存在。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="grid gap-2 text-sm sm:grid-cols-2">
              {data.coverage_gaps.map((gap) => (
                <li
                  key={gap}
                  className="rounded-lg bg-amber-50 px-3 py-2 text-amber-950"
                >
                  {gapLabel(gap)}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <SourceIndependenceCard
        groups={originGroups}
        evidenceGroupCount={evidenceGroupCount}
        referenceCount={data.evidence.length}
      />

      <ReportComparisonCard
        reportId={reportId}
        reports={reports}
        onSelectReport={onSelectReport}
      />

      <MetricComparisonsCard comparisons={comparisons} />

      <UsageSummaryCard
        usage={data.usage}
        title="本次研究用量"
        description="来自本 Run 已保存的模型调用；未提供费用的调用保持未知，不按 0 计算。"
        footer={
          <Link
            to={`/runs?run=${data.report.run_id}`}
            className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          >
            查看 Run 步骤与模型调用
          </Link>
        }
      />

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Layers3 aria-hidden="true" className="size-4 text-primary" />
            <CardTitle>证据包</CardTitle>
            <Badge variant="outline">{data.evidence.length} 个引用</Badge>
          </div>
          <CardDescription>
            先显示报告保存时实际引用的片段或 Fact，再提供 Document 原件入口。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {data.evidence.length ? (
            <div className="space-y-4">
              {data.evidence.map((evidence, index) => (
                <EvidenceCard
                  key={`${evidence.document_id}-${evidence.chunk_id}`}
                  evidence={evidence}
                  cited={cited.has(evidence.chunk_id)}
                  originGroup={
                    evidence.origin_id
                      ? originById.get(evidence.origin_id)
                      : undefined
                  }
                  index={index}
                />
              ))}
            </div>
          ) : (
            <EmptyPanel
              title="证据包没有可读取引用"
              description="报告仍可阅读，但无法追溯本次结论使用的片段；请结合资料缺口判断。"
            />
          )}
        </CardContent>
      </Card>

      {data.latest_collection_states.length ? (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Calendar aria-hidden="true" className="size-4 text-primary" />
              <CardTitle>当前采集指针</CardTitle>
            </div>
            <CardDescription>
              这些指向最新采集版本，仅用于说明后续变化；不会替换上方报告实际引用。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.latest_collection_states.map((state) => (
              <div
                key={`${state.source_id}-${state.dedupe_key}`}
                className="rounded-lg border border-border p-3 text-xs"
              >
                <p className="font-medium">
                  {state.source_id} / {state.dedupe_key}
                </p>
                <p className="mt-1 text-muted-foreground">
                  最新 Document{" "}
                  <span className="font-code">
                    {shortId(state.latest_document_id)}
                  </span>{" "}
                  · 完整度 {statusLabel(state.completeness)} · 最近尝试{" "}
                  {formatDateTime(state.last_attempted_at)}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}
    </article>
  );
}

function originKindLabel(kind: string): string {
  switch (kind) {
    case "canonical_url":
      return "页面声明的 canonical 链接";
    case "article_url":
      return "同一文章地址";
    case "external_id":
      return "同一 feed 条目标识";
    default:
      return kind;
  }
}

function EvidenceCard({
  evidence,
  cited,
  originGroup,
  index,
}: {
  evidence: ReportEvidence;
  cited: boolean;
  index: number;
  originGroup?: OriginGroup;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-3 py-2.5">
        <span className="font-code text-xs text-muted-foreground">
          E{index + 1}
        </span>
        <p className="min-w-0 flex-1 font-medium">
          {evidence.document.title}
        </p>
        {cited ? <Badge variant="info">报告已引用</Badge> : null}
        {originGroup?.merged ? (
          <Badge variant="outline">
            同一原件（{originGroup.document_count} 份）
          </Badge>
        ) : null}
        {evidence.chunk_available ? (
          <Badge variant="success">片段可读</Badge>
        ) : (
          <Badge variant="destructive">片段缺失</Badge>
        )}
      </div>
      <div className="space-y-4 p-3">
        <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
          <p>
            <span className="text-muted-foreground">来源：</span>
            {evidence.document.source_id}
          </p>
          {evidence.origin?.kind ? (
            <p>
              <span className="text-muted-foreground">来源声明：</span>
              {originKindLabel(evidence.origin.kind)}
            </p>
          ) : null}
          <p>
            <span className="text-muted-foreground">引用版本：</span>v
            {evidence.cited_document_version ?? "未记录"}
            {evidence.version_matches ? (
              <Badge variant="success" className="ml-2">
                一致
              </Badge>
            ) : (
              <Badge variant="destructive" className="ml-2">
                不一致
              </Badge>
            )}
          </p>
          <p>
            <span className="text-muted-foreground">可读版本：</span>v
            {evidence.document_version ?? "不可读"}
          </p>
          <p>
            <span className="text-muted-foreground">位置：</span>
            {evidence.position === null
              ? "未记录"
              : `片段 ${evidence.position + 1}`}
          </p>
          <p>
            <span className="text-muted-foreground">发布时间：</span>
            {formatDateTime(evidence.document.published_at)}
          </p>
          <p>
            <span className="text-muted-foreground">采集时间：</span>
            {formatDateTime(evidence.document.collected_at)}
          </p>
          <p>
            <span className="text-muted-foreground">完整度：</span>
            {statusLabel(evidence.document.completeness)}
          </p>
          <p>
            <span className="text-muted-foreground">解析批次：</span>{" "}
            {evidence.parse_is_current ? "当前成功批次" : "历史批次"}
          </p>
          <p>
            <span className="text-muted-foreground">采集指针：</span>{" "}
            {evidence.is_current_collection_pointer
              ? "当前最新"
              : "历史版本"}
          </p>
        </div>

        <div className="rounded-lg bg-background p-3 text-sm leading-7">
          {evidence.chunk_available && evidence.text ? (
            <p className="whitespace-pre-wrap">{evidence.text}</p>
          ) : (
            <p className="text-destructive">
              报告保存的片段无法从当前仓库读取。仍然保留其来源、版本和时间信息。
            </p>
          )}
          {evidence.locator !== null && evidence.locator !== undefined ? (
            <details className="mt-3 text-xs text-muted-foreground">
              <summary className="cursor-pointer font-medium text-foreground">
                原始定位
              </summary>
              <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-muted p-3 whitespace-pre-wrap">
                {formatValue(evidence.locator)}
              </pre>
            </details>
          ) : null}
        </div>

        {evidence.facts?.length ? (
          <div className="space-y-2">
            <p className="flex items-center gap-2 text-sm font-medium">
              <ShieldCheck aria-hidden="true" className="size-4 text-primary" />
              关联 Fact
            </p>
            <FactList facts={evidence.facts} />
          </div>
        ) : null}

        <div className="border-t border-border pt-3">
          <p className="mb-2 text-xs font-medium">在确认片段与版本后查看原件</p>
          <OriginalLinks original={evidence.original} />
        </div>
      </div>
    </section>
  );
}
