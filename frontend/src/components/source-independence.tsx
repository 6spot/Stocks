import type { MetricComparison, OriginGroup } from "@/api/schemas";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatValue } from "@/lib/format";

/**
 * Source independence and cross-source conflicts.
 *
 * Every number here comes from the stored Evidence Package, not from the report
 * text: the counts describe how many independent originals the selection
 * reached, and each conflicting value is listed exactly as it was published.
 */
export function SourceIndependenceSection({
  groups,
  evidenceGroupCount,
  referenceCount,
}: {
  groups: OriginGroup[];
  evidenceGroupCount: number | null;
  referenceCount: number;
}) {
  const merged = groups.filter((group) => group.merged);
  const coveredDocuments = groups.reduce(
    (total, group) => total + group.document_count,
    0,
  );
  return (
    <section>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">来源独立性</h2>
        <Badge variant="outline">{groups.length} 个独立原件</Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        同一原件的多份资料按一份独立证据计数；条数增加不会提高结论确定性。
      </p>
      <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-muted-foreground">独立原件</dt>
          <dd className="mt-0.5">{groups.length}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">资料版本</dt>
          <dd className="mt-0.5">{coveredDocuments || referenceCount}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">已合并同源组</dt>
          <dd className="mt-0.5">{merged.length}</dd>
        </div>
      </dl>
      {evidenceGroupCount !== null && evidenceGroupCount !== groups.length ? (
        <p className="mt-3 rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
          本包共有 {evidenceGroupCount} 个资料组、{referenceCount} 个引用，其中{" "}
          {groups.length} 个独立原件；这些数字不同正是同源合并的结果。
        </p>
      ) : null}
      {merged.length ? (
        <ul className="mt-3 grid gap-2">
          {merged.map((group) => (
            <li
              key={group.origin_id}
              className="border-l-2 border-l-border pl-3 text-xs"
            >
              <p className="font-medium">
                {group.origin_id}：{group.document_count} 份资料为同一原件
                {group.kind === "canonical_url" ? "（来源声明同一 canonical 链接）" : null}
              </p>
              <p className="mt-1 break-all text-muted-foreground">
                来源：{group.source_ids.join("、") || "未知"}
                {group.url ? ` · ${group.url}` : ""}
              </p>
              <p className="mt-1 text-muted-foreground">
                这些副本仍分别列在下方证据包中，可逐一查看或下载原件。
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          本次证据没有确认同源转载；这不表示各资料彼此独立，只表示来源没有声明相同原件。
        </p>
      )}
    </section>
  );
}

/**
 * Conflicting values for the same metric, one row per published value.
 */
export function MetricComparisonsSection({
  comparisons,
}: {
  comparisons: MetricComparison[];
}) {
  if (!comparisons.length) {
    return null;
  }
  return (
    <section>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">同指标取值对比</h2>
        <Badge variant="outline">{comparisons.length} 组</Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        同一指标的冲突值、不同口径与不同观测期并列显示；不做平均，也不标为已核实。
      </p>
      <div className="mt-4">
        {comparisons.map((comparison) => (
          <div
            key={comparison.comparison_id}
            className="mb-5 last:mb-0"
          >
            <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
              <span>{comparison.metric_id}</span>
              {comparison.symbol ? (
                <Badge variant="outline">{comparison.symbol}</Badge>
              ) : null}
              {comparison.value_conflict ? (
                <Badge variant="destructive">数值冲突</Badge>
              ) : null}
              {comparison.basis_mismatch ? (
                <Badge variant="outline">口径不同</Badge>
              ) : null}
              {comparison.period_mismatch ? (
                <Badge variant="outline">观测期不同</Badge>
              ) : null}
            </div>
            <div className="mt-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>数值</TableHead>
                    <TableHead>口径</TableHead>
                    <TableHead>观测期</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {comparison.facts.map((fact, index) => (
                    <TableRow
                      key={`${fact.fact_id ?? comparison.comparison_id}-${index}`}
                    >
                      <TableCell className="font-code text-xs tabular-nums">
                        {formatValue(fact.value)}
                        {fact.unit ? ` ${fact.unit}` : ""}
                        {fact.currency ? ` ${fact.currency}` : ""}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {fact.statement ?? "口径未记录"}
                      </TableCell>
                      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                        {fact.period_start ?? "未知"} –{" "}
                        {fact.period_end ?? "未知"}
                        <span className="mt-0.5 block">
                          可用 {fact.available_at ?? "未知"}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
