import type { ProcessingStage } from "@/api/schemas";
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
import { formatDateTime } from "@/lib/format";

// The stored classification value carries label ids, entities with verified
// original-text quotes, and model/taxonomy identity. Everything renders from
// the stored payload; nothing here infers or reinterprets labels.
type ClassificationPayload = {
  category?: unknown;
  event_type?: unknown;
  topics?: unknown;
  related_industries?: unknown;
  regions?: unknown;
  companies?: unknown;
  symbols?: unknown;
  reason?: unknown;
  should_extract?: unknown;
  taxonomy_version?: unknown;
  classified_at?: unknown;
  reason_or_evidence_ref?: {
    entity_evidence?: unknown;
    input_characters?: unknown;
    truncated?: unknown;
  } | null;
  attempts?: unknown;
};

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function entityEvidence(value: unknown): Array<{
  kind: string;
  value: string;
  quote: string;
}> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (
      item,
    ): item is { kind: string; value: string; quote: string } =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as { kind?: unknown }).kind === "string" &&
      typeof (item as { value?: unknown }).value === "string" &&
      typeof (item as { quote?: unknown }).quote === "string",
  );
}

function LabelRow({ label, values }: { label: string; values: string[] }) {
  if (!values.length) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      {values.map((value) => (
        <Badge key={value} variant="outline" className="font-code">
          {value}
        </Badge>
      ))}
    </div>
  );
}

function classifyStage(
  stages: ProcessingStage[],
): ProcessingStage | undefined {
  return stages.find((stage) => stage.stage === "classify");
}

export function ClassificationCard({
  classification,
  stages,
}: {
  classification: unknown;
  stages: ProcessingStage[];
}) {
  const stage = classifyStage(stages);
  const payload =
    typeof classification === "object" && classification !== null
      ? (classification as ClassificationPayload)
      : null;

  if (!payload) {
    // No stored result: the processing state decides what "nothing" means.
    // A failed retry keeps any earlier successful classification readable,
    // so this branch only runs when no result was ever stored.
    const status = stage?.status ?? "missing";
    return (
      <section className="space-y-3" aria-label="分类与实体">
        <h3 className="text-sm font-semibold">分类与实体</h3>
        {status === "failed" ? (
          <p className="rounded-lg bg-destructive/5 p-3 text-xs text-destructive">
            分类处理失败：
            {stage?.error?.message ?? stage?.error?.code ?? "阶段失败"}
            （尝试 {stage?.attempts ?? 0} 次）。此前没有已保存的分类结果。
          </p>
        ) : status === "missing" ? (
          <EmptyPanel title="尚未分类" description="该版本还没有分类记录。" />
        ) : (
          <p className="text-sm text-muted-foreground">
            分类尚未完成，等待后台处理
            {stage?.wait_reason ? `（${stage.wait_reason.replaceAll("_", " ")}）` : ""}
            。
          </p>
        )}
      </section>
    );
  }

  const category = typeof payload.category === "string" ? payload.category : null;
  const eventType =
    typeof payload.event_type === "string" ? payload.event_type : null;
  const companies = stringList(payload.companies);
  const symbols = stringList(payload.symbols);
  const evidence = entityEvidence(payload.reason_or_evidence_ref?.entity_evidence);
  const truncated = payload.reason_or_evidence_ref?.truncated === true;
  const inputCharacters = payload.reason_or_evidence_ref?.input_characters;
  const attempts = Array.isArray(payload.attempts) ? payload.attempts : [];
  const shouldExtract = payload.should_extract === true;
  const failedStage = stage?.status === "failed";

  return (
    <section className="space-y-3" aria-label="分类与实体">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">分类与实体</h3>
        {failedStage ? (
          <Badge variant="destructive">最新一次分类尝试失败</Badge>
        ) : null}
      </div>
      {failedStage ? (
        <p className="rounded-lg bg-destructive/5 p-3 text-xs text-destructive">
          最新一次分类尝试失败：
          {stage?.error?.message ?? stage?.error?.code ?? "阶段失败"}。
          下方展示的是此前已保存的分类结果，未被失败尝试覆盖。
        </p>
      ) : null}
      <div className="space-y-2">
        {category ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">类别</span>
            <Badge variant="secondary" className="font-code">
              {category}
            </Badge>
            {eventType ? (
              <Badge variant="secondary" className="font-code">
                {eventType}
              </Badge>
            ) : null}
          </div>
        ) : null}
        <LabelRow label="主题" values={stringList(payload.topics)} />
        <LabelRow
          label="行业"
          values={stringList(payload.related_industries)}
        />
        <LabelRow label="地域" values={stringList(payload.regions)} />
        <p className="text-xs text-muted-foreground">
          主题、行业与地域是背景相关性标签，不表示文中提及公司或代码。
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">公司</span>
          {companies.length ? (
            companies.map((value) => (
              <Badge key={value} variant="info" className="font-code">
                {value}
              </Badge>
            ))
          ) : (
            <span className="text-xs text-muted-foreground">
              无（未从原文抽出公司名）
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">股票代码</span>
          {symbols.length ? (
            symbols.map((value) => (
              <Badge key={value} variant="info" className="font-code">
                {value}
              </Badge>
            ))
          ) : (
            <span className="text-xs text-muted-foreground">
              无（原文未明确代码；不从公司名或行业推断）
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {shouldExtract
            ? "已判定：包含值得结构化的可抽取事实。"
            : "已判定：无需抽取结构化事实。"}
          实体与标签是模型判断，不是已核实的投资事实。
        </p>
      </div>
      {typeof payload.reason === "string" && payload.reason ? (
        <div>
          <p className="text-xs font-medium text-foreground">分类理由（模型）</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {payload.reason}
          </p>
        </div>
      ) : null}
      {evidence.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>类型</TableHead>
              <TableHead>实体</TableHead>
              <TableHead>原文依据</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {evidence.map((item, index) => (
              <TableRow key={`${item.kind}-${item.value}-${index}`}>
                <TableCell className="text-xs">
                  {item.kind === "company" ? "公司" : "代码"}
                </TableCell>
                <TableCell className="text-xs font-medium font-code">
                  {item.value}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  “{item.quote}”
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
      <div className="space-y-1 text-xs text-muted-foreground">
        <p>
          词表版本：<span className="font-code">{String(payload.taxonomy_version ?? "未记录")}</span>
          · 分类时间：{formatDateTime(
            typeof payload.classified_at === "string" ? payload.classified_at : null,
          )}
          {truncated && typeof inputCharacters === "number"
            ? ` · 分类输入已截断（${inputCharacters} 字符）`
            : ""}
        </p>
        {attempts.length ? (
          <p>
            模型调用：
            {attempts
              .filter(
                (item): item is { role: string; reported_model_name?: unknown; status?: unknown } =>
                  typeof item === "object" && item !== null,
              )
              .map((attempt) => {
                const role =
                  attempt.role === "classification_entities"
                    ? "实体抽取"
                    : "标签判断";
                const model =
                  typeof attempt.reported_model_name === "string"
                    ? attempt.reported_model_name
                    : "未知模型";
                const status = attempt.status === "succeeded" ? "成功" : "失败";
                return `${role} ${model} ${status}`;
              })
              .join("；")}
          </p>
        ) : null}
      </div>
    </section>
  );
}
