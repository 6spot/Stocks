import { Landmark } from "lucide-react";
import type { Fact } from "@/api/schemas";
import { EmptyPanel } from "@/components/async-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, formatValue, shortId } from "@/lib/format";

function FactCard({ fact }: { fact: Fact }) {
  return (
    <Card size="sm" className="bg-background/70">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Landmark aria-hidden="true" className="size-4 text-primary" />
          <CardTitle>{fact.statement}</CardTitle>
          <Badge variant="outline">{fact.fact_type}</Badge>
          {fact.symbol ? <Badge>{fact.symbol}</Badge> : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <p>
            <span className="text-muted-foreground">数值：</span>
            <span className="font-code">
              {formatValue(fact.value)}
              {fact.unit ? ` ${fact.unit}` : ""}
            </span>
          </p>
          <p>
            <span className="text-muted-foreground">指标：</span>
            {fact.metric_id ?? "未标注"}
          </p>
          <p>
            <span className="text-muted-foreground">观测期：</span>
            {formatDateTime(fact.period_start)} –{" "}
            {formatDateTime(fact.period_end)}
          </p>
          <p>
            <span className="text-muted-foreground">可用时间：</span>
            {formatDateTime(fact.available_at)}
          </p>
          <p>
            <span className="text-muted-foreground">提取方式：</span>
            {fact.origin_method}
          </p>
          <p>
            <span className="text-muted-foreground">Fact ID：</span>
            <span className="font-code">{shortId(fact.id)}</span>
          </p>
        </div>
        {fact.evidence_ref !== null && fact.evidence_ref !== undefined ? (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer font-medium text-foreground">
              证据定位
            </summary>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-muted p-3 whitespace-pre-wrap">
              {formatValue(fact.evidence_ref)}
            </pre>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function FactList({ facts }: { facts: Fact[] }) {
  if (!facts.length) {
    return (
      <EmptyPanel
        title="尚无已保存 Fact"
        description="这不是“事实不存在”，而是当前 Document 版本还没有可读取的结构化抽取结果。"
      />
    );
  }
  return (
    <div className="grid gap-3">
      {facts.map((fact) => (
        <FactCard key={fact.id} fact={fact} />
      ))}
    </div>
  );
}
