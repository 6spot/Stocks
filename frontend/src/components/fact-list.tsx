import type { Fact } from "@/api/schemas";
import { EmptyPanel } from "@/components/async-state";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime, formatValue, shortId } from "@/lib/format";

function conflictBadges(fact: Fact) {
  return (
    <>
      {fact.value_conflict ? (
        <Badge variant="destructive">与其他来源数值冲突</Badge>
      ) : null}
      {fact.basis_mismatch ? (
        <Badge variant="outline">口径与其他来源不同</Badge>
      ) : null}
      {!fact.value_conflict && !fact.basis_mismatch && fact.conflict ? (
        <Badge variant="destructive">同指标取值不一致</Badge>
      ) : null}
    </>
  );
}

/**
 * Structured facts as one row per fact: metric, value, unit, observation
 * window, locator and extraction method. Conflicts stay inline on the row.
 */
export function FactTable({ facts }: { facts: Fact[] }) {
  if (!facts.length) {
    return (
      <EmptyPanel
        title="尚无已保存 Fact"
        description="这不是“事实不存在”，而是当前 Document 版本还没有可读取的结构化抽取结果。"
      />
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>指标</TableHead>
          <TableHead>数值</TableHead>
          <TableHead>单位</TableHead>
          <TableHead>观测期</TableHead>
          <TableHead>定位</TableHead>
          <TableHead>提取方式</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {facts.map((fact) => (
          <TableRow key={fact.id}>
            <TableCell className="max-w-64 text-xs">
              <p className="font-medium break-words">
                {fact.metric_id ?? "未标注"}
              </p>
              {fact.statement ? (
                <p className="mt-1 break-words text-muted-foreground">
                  {fact.statement}
                </p>
              ) : null}
              <p className="mt-1 text-muted-foreground">
                {fact.fact_type}
                {fact.symbol ? ` · ${fact.symbol}` : ""}
              </p>
              {conflictBadges(fact)}
            </TableCell>
            <TableCell className="font-code text-xs tabular-nums">
              {formatValue(fact.value)}
            </TableCell>
            <TableCell className="text-xs">
              {fact.unit ?? ""}
              {fact.currency ? ` ${fact.currency}` : ""}
            </TableCell>
            <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
              {formatDateTime(fact.period_start)} –{" "}
              {formatDateTime(fact.period_end)}
              <span className="mt-0.5 block">
                可用 {formatDateTime(fact.available_at)}
              </span>
            </TableCell>
            <TableCell className="text-xs">
              {fact.evidence_ref !== null && fact.evidence_ref !== undefined ? (
                <details className="text-muted-foreground">
                  <summary className="cursor-pointer font-medium text-foreground">
                    {shortId(fact.id)}
                  </summary>
                  <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-muted p-2 whitespace-pre-wrap">
                    {formatValue(fact.evidence_ref)}
                  </pre>
                </details>
              ) : (
                <span className="font-code text-muted-foreground">
                  {shortId(fact.id)}
                </span>
              )}
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              {fact.origin_method}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
