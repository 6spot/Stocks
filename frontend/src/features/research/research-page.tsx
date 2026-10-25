import {
  AlertTriangle,
  ArrowLeft,
  FileText,
  GitCompareArrows,
} from "lucide-react";
import { useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { dashboardApi } from "@/api/dashboard";
import {
  evidenceCoverageSchema,
  type OriginGroup,
  type ReportDetail,
  type ReportEvidence,
  type ReportListItem,
} from "@/api/schemas";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { FactTable } from "@/components/fact-list";
import {
  MetricComparisonsSection,
  SourceIndependenceSection,
} from "@/components/source-independence";
import { OriginalLinks } from "@/components/original-links";
import { PageHeader } from "@/components/page-header";
import { ReportComparisonView } from "@/components/report-comparison";
import { Section } from "@/components/section";
import { StatusBadge } from "@/components/status-badge";
import { UsageSummarySection } from "@/components/usage-summary";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatValue } from "@/lib/format";
import { gapLabel, statusLabel } from "@/lib/labels";
import { renderMarkdown } from "@/lib/markdown";

const SUPPORT_TABS = ["evidence", "sources", "usage"] as const;
type SupportTab = (typeof SUPPORT_TABS)[number];

function parseSupportTab(value: string): SupportTab {
  return SUPPORT_TABS.includes(value as SupportTab)
    ? (value as SupportTab)
    : "evidence";
}

function reportPath(reportId: string): string {
  return `/research/${encodeURIComponent(reportId)}`;
}

export function ResearchPage() {
  const navigate = useNavigate();
  const reports = useResource(
    (signal) => dashboardApi.reports(100, signal),
    [],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="研究报告"
        description="统一阅读市场、行业和个股研究；先核对报告实际使用的证据版本，再打开原件。"
      />

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
        <Table aria-label="报告列表">
          <TableHeader>
            <TableRow>
              <TableHead>报告标题</TableHead>
              <TableHead>任务</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">生成时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {reports.data.items.map((report) => (
              <TableRow
                key={report.report_id}
                className="cursor-pointer hover:bg-muted/60"
                onClick={() => navigate(reportPath(report.report_id))}
              >
                <TableCell className="max-w-72">
                  <button
                    type="button"
                    aria-label={`打开报告 ${report.title}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      navigate(reportPath(report.report_id));
                    }}
                    className="press flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-sm text-left focus-visible:border-ring focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                  >
                    <FileText
                      aria-hidden="true"
                      className="size-4 shrink-0 text-muted-foreground"
                    />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">
                      {report.title}
                    </span>
                  </button>
                </TableCell>
                <TableCell className="font-code text-xs text-muted-foreground">
                  {report.task_id}
                </TableCell>
                <TableCell>
                  <StatusBadge status={report.run_status} />
                </TableCell>
                <TableCell className="whitespace-nowrap text-right text-xs text-muted-foreground">
                  {formatDateTime(report.created_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

export function ResearchDetailPage() {
  const { reportId = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const reports = useResource(
    (signal) => dashboardApi.reports(100, signal),
    [],
  );
  // 对照对象只能来自成功保存的报告，失败 Run 不进入对照下拉。
  const succeededReports =
    reports.data?.items.filter((item) => item.run_status === "succeeded") ??
    [];

  function backToList() {
    if (window.history.state && window.history.state.idx > 0) {
      navigate(-1);
    } else {
      navigate("/research");
    }
  }

  function selectReport(nextReportId: string) {
    navigate(reportPath(nextReportId));
  }

  function enterCompare(otherReportId: string) {
    navigate(
      `${reportPath(reportId)}?compare=${encodeURIComponent(otherReportId)}`,
      { replace: true },
    );
  }

  function exitCompare() {
    navigate(reportPath(reportId), { replace: true });
  }

  if (!reportId) {
    return (
      <ErrorPanel
        title="报告地址无效"
        message="地址缺少 report_id；请从研究报告列表选择一份报告。"
      />
    );
  }

  return (
    <div className="space-y-5">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="-ml-2 text-muted-foreground"
        onClick={backToList}
      >
        <ArrowLeft aria-hidden="true" />
        返回列表
      </Button>
      <ReportReader
        key={reportId}
        reportId={reportId}
        compareReportId={params.get("compare") ?? undefined}
        reports={succeededReports}
        onSelectReport={selectReport}
        onEnterCompare={enterCompare}
        onExitCompare={exitCompare}
      />
    </div>
  );
}

function ReportReader({
  reportId,
  compareReportId,
  reports = [],
  onSelectReport,
  onEnterCompare,
  onExitCompare,
}: {
  reportId: string;
  compareReportId?: string;
  reports?: ReportListItem[];
  onSelectReport?: (reportId: string) => void;
  onEnterCompare?: (otherReportId: string) => void;
  onExitCompare?: () => void;
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
  if (compareReportId && compareReportId !== reportId) {
    return (
      <ReportComparisonView
        reportId={reportId}
        otherReportId={compareReportId}
        reports={reports}
        onSelectReport={onSelectReport}
        onChangeOther={(otherId) => onEnterCompare?.(otherId)}
        onExit={onExitCompare}
      />
    );
  }
  return (
    <ReportBody
      data={data}
      reports={reports}
      onEnterCompare={onEnterCompare}
    />
  );
}

function ReportBody({
  data,
  reports,
  onEnterCompare,
}: {
  data: ReportDetail;
  reports: ReportListItem[];
  onEnterCompare?: (otherReportId: string) => void;
}) {
  // Inactive tab panels render null so only the visible section mounts and
  // no hidden section can trigger requests.
  const [supportTab, setSupportTab] = useState<SupportTab>("evidence");
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
  const otherReports = reports.filter(
    (report) => report.report_id !== data.report.report_id,
  );
  const preferredCompareId =
    otherReports.find((report) => report.task_id === data.report.task_id)
      ?.report_id ?? otherReports[0]?.report_id;
  // 截断与资料缺口合并成一句内联提醒，避免在正文前堆叠大块警告。
  const warnings: string[] = [];
  if (data.evidence_package.retrieval_truncated) {
    warnings.push("证据检索已截断，报告未读取所有候选资料");
  }
  if (data.coverage_gaps.length) {
    warnings.push(
      `资料缺口：${data.coverage_gaps.map(gapLabel).join("、")}，不代表资料在现实中不存在`,
    );
  }
  return (
    <article className="space-y-6">
      <header>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-xl font-semibold leading-tight tracking-tight">
            {data.report.title}
          </h1>
          {data.run && typeof data.run === "object" && "status" in data.run ? (
            <StatusBadge status={String(data.run.status)} />
          ) : null}
          <span className="text-xs text-muted-foreground">
            生成于 {formatDateTime(data.report.created_at)}
          </span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground tabular-nums">
          独立原件 {originGroups.length ? originGroups.length : "未知"} · 引用{" "}
          {data.evidence.length} 条
        </p>
        {warnings.length ? (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 size-3.5 shrink-0"
            />
            <span>{warnings.join("；")}。</span>
          </p>
        ) : null}
        <div
          className="report-prose mt-4 max-w-[75ch]"
          dangerouslySetInnerHTML={{
            __html: renderMarkdown(data.report.content),
          }}
        />
      </header>

      <div className="mt-8">
        <h2 className="text-sm font-semibold text-foreground">支撑信息</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          报告引用的证据、来源质量与本次研究用量；按需展开。
        </p>
      </div>
      <Tabs
        value={supportTab}
        onValueChange={(value) => setSupportTab(parseSupportTab(String(value)))}
      >
        <TabsList aria-label="报告支撑信息">
          <TabsTrigger value="evidence">证据包</TabsTrigger>
          <TabsTrigger value="sources">来源与指标</TabsTrigger>
          <TabsTrigger value="usage">研究用量</TabsTrigger>
        </TabsList>
        <TabsContent value="evidence">
          {supportTab === "evidence" ? (
            <Section
              title="证据包"
              description="先显示报告保存时实际引用的片段或 Fact，再提供 Document 原件入口。"
              actions={
                <Badge variant="outline">{data.evidence.length} 个引用</Badge>
              }
            >
              {data.evidence.length ? (
                <ol className="divide-y divide-border">
                  {data.evidence.map((evidence, index) => (
                    <li
                      key={`${evidence.document_id}-${evidence.chunk_id}`}
                      className="py-4 first:pt-0 last:pb-0"
                    >
                      <EvidenceRow
                        evidence={evidence}
                        cited={cited.has(evidence.chunk_id)}
                        originGroup={
                          evidence.origin_id
                            ? originById.get(evidence.origin_id)
                            : undefined
                        }
                        index={index}
                      />
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyPanel
                  title="证据包没有可读取引用"
                  description="报告仍可阅读，但无法追溯本次结论使用的片段；请结合资料缺口判断。"
                />
              )}
            </Section>
          ) : null}
        </TabsContent>
        <TabsContent value="sources">
          {supportTab === "sources" ? (
            <div className="space-y-8">
              <SourceIndependenceSection
                groups={originGroups}
                evidenceGroupCount={evidenceGroupCount}
                referenceCount={data.evidence.length}
              />
              <MetricComparisonsSection comparisons={comparisons} />
            </div>
          ) : null}
        </TabsContent>
        <TabsContent value="usage">
          {supportTab === "usage" ? (
            <UsageSummarySection
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
          ) : null}
        </TabsContent>
      </Tabs>

      {preferredCompareId && onEnterCompare ? (
        <div className="border-t border-border pt-4">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onEnterCompare(preferredCompareId)}
          >
            <GitCompareArrows aria-hidden="true" />
            对照其他报告
          </Button>
          <p className="mt-2 text-xs text-muted-foreground">
            与另一份成功报告做前后对照。
          </p>
        </div>
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

function EvidenceRow({
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
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-code text-xs text-muted-foreground">
          E{index + 1}
        </span>
        <p className="min-w-0 flex-1 text-sm font-medium">
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

      <div className="text-sm leading-7">
        {evidence.chunk_available && evidence.text ? (
          <p className="whitespace-pre-wrap">{evidence.text}</p>
        ) : (
          <p className="text-destructive">
            报告保存的片段无法从当前仓库读取。仍然保留其来源、版本和时间信息。
          </p>
        )}
        {evidence.locator !== null && evidence.locator !== undefined ? (
          <details className="mt-2 text-xs text-muted-foreground">
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
          <p className="text-sm font-medium">关联 Fact</p>
          <FactTable facts={evidence.facts} />
        </div>
      ) : null}

      <div className="border-t border-border pt-3">
        <p className="mb-2 text-xs font-medium">在确认片段与版本后查看原件</p>
        <OriginalLinks original={evidence.original} />
      </div>
    </section>
  );
}
