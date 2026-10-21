import { GitMerge, Scale } from "lucide-react";
import type { MetricComparison, OriginGroup } from "@/api/schemas";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatValue } from "@/lib/format";

/**
 * Source independence and cross-source conflicts.
 *
 * Every number here comes from the stored Evidence Package, not from the report
 * text: the counts describe how many independent originals the selection
 * reached, and each conflicting value is listed exactly as it was published.
 */
export function SourceIndependenceCard({
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
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <GitMerge aria-hidden="true" className="size-4 text-primary" />
          <CardTitle>来源独立性</CardTitle>
          <Badge variant="outline">{groups.length} 个独立原件</Badge>
        </div>
        <CardDescription>
          同一原件的多份资料按一份独立证据计数；条数增加不会提高结论确定性。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid gap-2 text-xs sm:grid-cols-3">
          <p>
            <span className="text-muted-foreground">独立原件：</span>
            {groups.length}
          </p>
          <p>
            <span className="text-muted-foreground">资料版本：</span>
            {coveredDocuments || referenceCount}
          </p>
          <p>
            <span className="text-muted-foreground">已合并同源组：</span>
            {merged.length}
          </p>
        </div>
        {evidenceGroupCount !== null && evidenceGroupCount !== groups.length ? (
          <p className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
            本包共有 {evidenceGroupCount} 个资料组、{referenceCount} 个引用，其中{" "}
            {groups.length} 个独立原件；这些数字不同正是同源合并的结果。
          </p>
        ) : null}
        {merged.length ? (
          <ul className="grid gap-2">
            {merged.map((group) => (
              <li
                key={group.origin_id}
                className="rounded-lg border border-border p-3 text-xs"
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
          <p className="text-xs text-muted-foreground">
            本次证据没有确认同源转载；这不表示各资料彼此独立，只表示来源没有声明相同原件。
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function MetricComparisonsCard({
  comparisons,
}: {
  comparisons: MetricComparison[];
}) {
  if (!comparisons.length) {
    return null;
  }
  return (
    <Card className="border-amber-200">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Scale aria-hidden="true" className="size-4 text-primary" />
          <CardTitle>同指标取值对比</CardTitle>
          <Badge variant="outline">{comparisons.length} 组</Badge>
        </div>
        <CardDescription>
          同一指标的冲突值、不同口径与不同观测期并列显示；不做平均，也不标为已核实。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {comparisons.map((comparison) => (
          <div
            key={comparison.comparison_id}
            className="rounded-lg border border-border p-3"
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
            <ul className="mt-2 grid gap-1 text-xs">
              {comparison.facts.map((fact, index) => (
                <li
                  key={`${fact.fact_id ?? comparison.comparison_id}-${index}`}
                  className="rounded bg-muted/60 px-2 py-1"
                >
                  <span className="font-code">
                    {formatValue(fact.value)}
                    {fact.unit ? ` ${fact.unit}` : ""}
                    {fact.currency ? ` ${fact.currency}` : ""}
                  </span>
                  <span className="text-muted-foreground">
                    {" "}
                    · 观测期 {fact.period_start ?? "未知"} –{" "}
                    {fact.period_end ?? "未知"} · 可用{" "}
                    {fact.available_at ?? "未知"}
                  </span>
                </li>
              ))}
            </ul>
            {comparison.facts[0]?.statement ? (
              <p className="mt-2 text-xs text-muted-foreground">
                {comparison.facts[0].statement}
              </p>
            ) : null}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
