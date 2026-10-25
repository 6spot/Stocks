import { ExternalLink } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { dashboardApi, originalEndpoint } from "@/api/dashboard";
import type {
  ComparisonSide,
  ReportComparison,
  ReportListItem,
} from "@/api/schemas";
import { ErrorPanel, LoadingPanel } from "@/components/async-state";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatValue } from "@/lib/format";

/**
 * Compare two saved reports, earlier side first.
 *
 * Everything shown here comes from the two stored snapshots: each side keeps
 * its own as_of, coverage and cited Document versions, and a difference is
 * presented as new material or as a new source version rather than as
 * something the earlier report already knew.
 */
export function ReportComparisonSection({
  reportId,
  reports,
  onSelectReport,
}: {
  reportId: string;
  reports: ReportListItem[];
  onSelectReport?: (reportId: string) => void;
}) {
  const options = reports.filter((item) => item.report_id !== reportId);
  const current = reports.find((item) => item.report_id === reportId);
  const preferred = options.find(
    (item) => item.task_id === current?.task_id,
  );
  const [otherId, setOtherId] = useState(preferred?.report_id ?? "");

  if (!options.length) {
    return null;
  }
  return (
    <section>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">历史报告对照</h2>
        <Badge variant="outline">{options.length} 份可选</Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        只比较两份报告各自保存的分析、证据包与引用版本；不重跑研究，也不改写旧报告。
      </p>
      <Separator className="mt-3" />
      <div className="mt-4 space-y-4">
        <label className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">对比报告：</span>
          <select
            aria-label="对比报告"
            value={otherId}
            onChange={(event) => setOtherId(event.target.value)}
            className="h-9 min-w-64 rounded-lg border border-border bg-background px-2 text-sm"
          >
            <option value="">不对比</option>
            {options.map((item) => (
              <option key={item.report_id} value={item.report_id}>
                {item.task_id} · {formatDateTime(item.created_at)} · {item.title}
              </option>
            ))}
          </select>
        </label>
        {otherId ? (
          <ComparisonBody
            reportId={reportId}
            otherReportId={otherId}
            onSelectReport={onSelectReport}
          />
        ) : (
          <p className="text-xs text-muted-foreground">
            选择另一份成功报告后，这里会列出结论、证据版本、指标取值与覆盖差异。
          </p>
        )}
      </div>
    </section>
  );
}

function ComparisonBody({
  reportId,
  otherReportId,
  onSelectReport,
}: {
  reportId: string;
  otherReportId: string;
  onSelectReport?: (reportId: string) => void;
}) {
  const comparison = useResource(
    (signal) =>
      dashboardApi.reportComparison(reportId, otherReportId, signal),
    [reportId, otherReportId],
  );

  if (comparison.loading) {
    return <LoadingPanel label="正在比较两份报告" />;
  }
  if (comparison.error) {
    return (
      <ErrorPanel
        message={comparison.error.message}
        onRetry={comparison.refresh}
      />
    );
  }
  if (!comparison.data) {
    return null;
  }
  return (
    <ComparisonResult
      data={comparison.data}
      onSelectReport={onSelectReport}
    />
  );
}

function ComparisonResult({
  data,
  onSelectReport,
}: {
  data: ReportComparison;
  onSelectReport?: (reportId: string) => void;
}) {
  const documentsChanged =
    data.documents.appeared.count +
    data.documents.disappeared.count +
    data.documents.persisting.count;
  return (
    <div className="space-y-5 text-sm">
      <div className="grid gap-4 sm:grid-cols-2">
        <SidePanel label="较早报告" side={data.earlier} onSelectReport={onSelectReport} />
        <SidePanel label="较新报告" side={data.later} onSelectReport={onSelectReport} />
      </div>

      {data.warnings.length ? (
        <ul className="space-y-1 rounded-lg bg-muted p-3 text-xs">
          {data.warnings.map((warning) => (
            <li key={warning.code}>{warning.message}</li>
          ))}
        </ul>
      ) : null}

      <section className="space-y-2">
        <h3 className="text-sm font-medium">结论差异</h3>
        <FindingGroup
          title="新增结论"
          items={data.findings.appeared}
          render={(item) => <FindingLine finding={item} />}
        />
        <FindingGroup
          title="不再出现"
          items={data.findings.disappeared}
          render={(item) => <FindingLine finding={item} />}
        />
        <FindingGroup
          title="持续结论"
          items={data.findings.persisting}
          render={(item) => {
            const finding = item as {
              kind?: string | null;
              statement?: string | null;
              earlier_uncertainty?: string | null;
              later_uncertainty?: string | null;
              uncertainty_changed?: boolean;
              evidence_changed?: boolean;
            };
            return (
              <li className="text-xs">
                <p className="font-medium">{finding.statement}</p>
                <p className="mt-1 text-muted-foreground">
                  {finding.kind} · 不确定性：
                  {finding.uncertainty_changed ? "已变化" : "未变化"}
                  {finding.evidence_changed ? " · 引用证据变化" : ""}
                </p>
                {finding.uncertainty_changed ? (
                  <p className="mt-1 text-muted-foreground">
                    {finding.earlier_uncertainty} → {finding.later_uncertainty}
                  </p>
                ) : null}
              </li>
            );
          }}
        />
        <p className="text-xs text-muted-foreground">
          只有完全相同的结论类型与文字才视为同一结论；改写过的结论按一条消失、一条新增显示。
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">
          证据差异
          <span className="ml-2 text-xs text-muted-foreground">
            资料 {documentsChanged} 处变化
          </span>
        </h3>
        <FindingGroup
          title="新引用资料"
          items={data.documents.appeared}
          render={(item) => <DocumentLine document={item} />}
        />
        <FindingGroup
          title="不再引用"
          items={data.documents.disappeared}
          render={(item) => <DocumentLine document={item} />}
        />
        <FindingGroup
          title="同一来源的新版本"
          items={data.documents.persisting}
          render={(item) => {
            const entry = item as {
              title?: string | null;
              earlier?: { version?: number | null; document_id?: string };
              later?: { version?: number | null; document_id?: string };
              version_changed?: boolean;
              content_changed?: boolean;
            };
            return (
              <li className="text-xs">
                <p className="font-medium">{entry.title}</p>
                <p className="mt-1 text-muted-foreground">
                  v{entry.earlier?.version ?? "未知"} → v
                  {entry.later?.version ?? "未知"}
                  {entry.content_changed ? " · 原件字节已变化" : " · 原件字节未变化"}
                </p>
                <p className="mt-1 flex flex-wrap gap-2">
                  <DocumentLink documentId={entry.earlier?.document_id} label="打开较早原件" />
                  <DocumentLink documentId={entry.later?.document_id} label="打开较新原件" />
                </p>
              </li>
            );
          }}
        />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">
          指标取值变化
          <span className="ml-2 text-xs text-muted-foreground">
            {data.facts.metric_changes.count} 项
          </span>
        </h3>
        {data.facts.metric_changes.items.length ? (
          <ul className="space-y-3">
            {data.facts.metric_changes.items.map((change, index) => (
              <li
                key={`${change.metric_id}-${index}`}
                className="space-y-2 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{change.metric_id}</span>
                  {change.symbol ? (
                    <Badge variant="outline">{change.symbol}</Badge>
                  ) : null}
                  {change.value_changed ? (
                    <Badge variant="destructive">数值变化</Badge>
                  ) : null}
                  {change.basis_changed ? (
                    <Badge variant="outline">口径不同</Badge>
                  ) : null}
                  {change.period_changed ? (
                    <Badge variant="outline">观测期不同</Badge>
                  ) : null}
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <ValueList label="较早" values={change.earlier} />
                  <ValueList label="较新" values={change.later} />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">
            两份报告引用的同指标取值没有差异。
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          数值按各自来源原样并列，不做平均、不覆盖较早的取值，也不据此判定哪一次“已核实”。
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">
          晚于上一份 as_of 的资料
          <span className="ml-2 text-xs text-muted-foreground">
            {data.late_material.documents.count} 份
          </span>
        </h3>
        {data.late_material.documents.items.length ? (
          <ul className="space-y-2">
            {data.late_material.documents.items.map((document) => (
              <li
                key={`${document.document_id}-${document.collected_at}`}
                className="text-xs"
              >
                <p className="font-medium">{document.title}</p>
                <p className="mt-0.5 text-muted-foreground">
                  发布 {formatDateTime(document.published_at)} · 采集{" "}
                  {formatDateTime(document.collected_at)} · 上一份参考时间{" "}
                  {formatDateTime(data.late_material.earlier_as_of)}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">
            较新报告没有引用晚于上一份参考时间的资料。
          </p>
        )}
        <p className="text-xs text-muted-foreground">{data.late_material.note}</p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">覆盖差异</h3>
        <div className="grid gap-3 text-xs sm:grid-cols-2">
          <CoverageList label="新增缺口" items={data.coverage.gaps_added} />
          <CoverageList label="已消失缺口" items={data.coverage.gaps_removed} />
        </div>
        {data.coverage.unexamined_unknown.earlier ||
        data.coverage.unexamined_unknown.later ? (
          <p className="text-xs text-muted-foreground">
            至少一份报告的未扫描候选仍然未知，对照不把它当成 0；样本差异不能解释为事实变化。
          </p>
        ) : null}
      </section>

      {data.open_questions.appeared.count ||
      data.open_questions.disappeared.count ? (
        <section className="space-y-1 text-xs">
          <h3 className="text-sm font-medium">开放问题</h3>
          {data.open_questions.appeared.items.map((question) => (
            <p key={`new-${question}`}>新增：{question}</p>
          ))}
          {data.open_questions.disappeared.items.map((question) => (
            <p key={`gone-${question}`} className="text-muted-foreground">
              不再出现：{question}
            </p>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function SidePanel({
  label,
  side,
  onSelectReport,
}: {
  label: string;
  side: ComparisonSide;
  onSelectReport?: (reportId: string) => void;
}) {
  return (
    <div className="space-y-2 border-l-2 border-l-border pl-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{label}</Badge>
        <span className="text-xs text-muted-foreground">
          {formatDateTime(side.created_at)}
        </span>
      </div>
      <p className="font-medium">{side.title}</p>
      <p className="text-xs text-muted-foreground">
        as_of {formatDateTime(side.as_of)} · Task {side.task_id} · Run{" "}
        {side.run_status ?? "未知"}
      </p>
      <p className="line-clamp-4 text-xs">{side.summary}</p>
      <dl className="grid grid-cols-2 gap-1 text-xs">
        <Stat label="选入片段" value={side.coverage.selected_count} />
        <Stat label="证据组" value={side.coverage.evidence_group_count} />
        <Stat label="独立原件" value={side.coverage.origin_group_count} />
        <Stat label="补充输入" value={side.coverage.research_input_count} />
        <Stat label="已知缺口" value={side.coverage.gaps?.length ?? 0} />
        <Stat
          label="未扫描候选"
          value={side.coverage.unexamined_count ?? "未知"}
        />
      </dl>
      {onSelectReport ? (
        <button
          type="button"
          className={`${buttonVariants({ variant: "outline", size: "sm" })}`}
          onClick={() => onSelectReport(side.report_id)}
        >
          打开这份报告
        </button>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{value === null || value === undefined ? "未知" : String(value)}</dd>
    </div>
  );
}

function FindingGroup<T>({
  title,
  items,
  render,
}: {
  title: string;
  items: { count: number; items: T[]; truncated: boolean };
  render: (item: T) => ReactNode;
}) {
  if (!items.count) {
    return null;
  }
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">
        {title}（{items.count}）
        {items.truncated ? " · 仅显示前若干条" : ""}
      </p>
      <ul className="space-y-2">
        {items.items.map((item, index) => (
          <Fragment key={index}>{render(item)}</Fragment>
        ))}
      </ul>
    </div>
  );
}

function FindingLine({ finding }: { finding: unknown }) {
  const item = finding as {
    kind?: string | null;
    statement?: string | null;
    uncertainty?: string | null;
  };
  return (
    <li className="text-xs">
      <p className="font-medium">{item.statement}</p>
      <p className="mt-1 text-muted-foreground">
        {item.kind} · 不确定性：{item.uncertainty ?? "未记录"}
      </p>
    </li>
  );
}

function DocumentLine({ document }: { document: unknown }) {
  const item = document as {
    document_id?: string;
    title?: string | null;
    version?: number | null;
    published_at?: string | null;
    collected_at?: string | null;
    source_id?: string | null;
  };
  return (
    <li className="text-xs">
      <p className="font-medium">{item.title ?? item.document_id}</p>
      <p className="mt-1 text-muted-foreground">
        来源 {item.source_id ?? "未知"} · v{item.version ?? "未知"} · 发布{" "}
        {formatDateTime(item.published_at)} · 采集 {formatDateTime(item.collected_at)}
      </p>
      <DocumentLink documentId={item.document_id} label="打开原件" />
    </li>
  );
}

function DocumentLink({
  documentId,
  label,
}: {
  documentId?: string | null;
  label: string;
}) {
  if (!documentId) {
    return null;
  }
  return (
    <a
      href={originalEndpoint(documentId)}
      target="_blank"
      rel="noreferrer"
      className={buttonVariants({ variant: "ghost", size: "sm" })}
    >
      <ExternalLink aria-hidden="true" />
      {label}
    </a>
  );
}

function ValueList({
  label,
  values,
}: {
  label: string;
  values: Array<{
    value?: unknown;
    unit?: string | null;
    currency?: string | null;
    period_end?: string | null;
    document_id?: string | null;
  }>;
}) {
  return (
    <div className="rounded bg-muted/60 px-2 py-1">
      <p className="text-muted-foreground">{label}</p>
      {values.length ? (
        values.map((item, index) => (
          <p key={`${item.document_id}-${index}`}>
            <span className="font-code tabular-nums">
              {formatValue(item.value)}
              {item.unit ? ` ${item.unit}` : ""}
              {item.currency ? ` ${item.currency}` : ""}
            </span>
            <span className="text-muted-foreground">
              {" "}
              · 观测期至 {item.period_end ?? "未知"}
            </span>
          </p>
        ))
      ) : (
        <p className="text-muted-foreground">没有该指标的取值</p>
      )}
    </div>
  );
}

function CoverageList({
  label,
  items,
}: {
  label: string;
  items: { count: number; items: string[]; truncated: boolean };
}) {
  return (
    <div>
      <p className="text-muted-foreground">
        {label}（{items.count}）
      </p>
      {items.items.length ? (
        <ul className="mt-1 space-y-0.5">
          {items.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-muted-foreground">无</p>
      )}
    </div>
  );
}
