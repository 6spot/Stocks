import { useResource } from "@/hooks/use-resource";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { FactTable } from "@/components/fact-list";
import { OriginalLinks } from "@/components/original-links";
import { ProcessingStages } from "@/components/processing-stages";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime, formatValue, shortId } from "@/lib/format";

function LocatorDetails({ locator }: { locator: unknown }) {
  if (locator === null || locator === undefined) {
    return null;
  }
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="min-h-11 cursor-pointer py-3 font-medium text-foreground sm:min-h-0 sm:py-0">
        原始定位
      </summary>
      <p className="mt-1 leading-relaxed">
        HTML 的 <code>source_ranges</code> 指向源码中的实体范围；表格分隔符属于系统排版位置。
      </p>
      <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-muted p-3 whitespace-pre-wrap">
        {formatValue(locator)}
      </pre>
    </details>
  );
}

export function DocumentDetails({
  documentId,
  onReadContext,
}: {
  documentId: string;
  onReadContext?: (chunkId: string) => void;
}) {
  const detail = useResource(
    (signal) => dashboardApi.document(documentId, signal),
    [documentId],
  );
  const facts = useResource(
    (signal) => dashboardApi.documentFacts(documentId, signal),
    [documentId],
  );

  if (detail.loading) {
    return <LoadingPanel label="正在读取 Document 详情" />;
  }
  if (detail.error) {
    return (
      <ErrorPanel
        message={detail.error.message}
        onRetry={detail.refresh}
      />
    );
  }
  if (!detail.data) {
    return <EmptyPanel title="未选择 Document" />;
  }

  const data = detail.data;
  const document = data.document;

  return (
    <article className="space-y-5" aria-label="Document 详情">
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold leading-tight">
            {document.title}
          </h3>
          <Badge variant="outline">v{document.version}</Badge>
          <StatusBadge status={document.completeness} />
          {data.is_current_collection_pointer ? (
            <Badge variant="success">当前采集指针</Badge>
          ) : (
            <Badge variant="warning">历史版本</Badge>
          )}
        </div>
        <dl className="grid gap-3 text-xs sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">发布时间</dt>
            <dd>{formatDateTime(document.published_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">来源更新时间</dt>
            <dd>{formatDateTime(document.source_updated_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">采集时间</dt>
            <dd>{formatDateTime(document.collected_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">内容哈希</dt>
            <dd className="font-code break-all">
              {document.content_hash ?? "未记录"}
            </dd>
          </div>
        </dl>
        {document.fetch_error?.code || document.fetch_error?.message ? (
          <p className="rounded-lg bg-destructive/5 p-3 text-xs text-destructive">
            采集缺口：{document.fetch_error.message ?? document.fetch_error.code}
          </p>
        ) : null}
        {data.collection_state ? (
          <p className="text-xs text-muted-foreground">
            最新采集指针指向{" "}
            <span className="font-code">
              {shortId(data.collection_state.latest_document_id)}
            </span>{" "}
            · 最近尝试 {formatDateTime(data.collection_state.last_attempted_at)}
          </p>
        ) : null}
        <OriginalLinks original={data.original} />
      </section>

      <Separator />

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">处理状态</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            “已采集”不等于“可搜索”。解析、分类、事实抽取和索引分别记录。
          </p>
        </div>
        <ProcessingStages stages={data.processing_stages} />
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>批次</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>详情</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell className="text-xs font-medium">解析批次</TableCell>
              <TableCell>
                <StatusBadge status={data.parse_state?.status ?? "missing"} />
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                <span className="block">
                  当前成功批次：
                  <span className="font-code">
                    {shortId(data.parse_state?.current_batch_id ?? "无")}
                  </span>
                </span>
                <span className="block">
                  最新尝试批次：
                  <span className="font-code">
                    {shortId(data.parse_state?.latest_batch_id ?? "无")}
                  </span>
                </span>
                <span className="block">
                  尝试时间：{formatDateTime(data.parse_state?.attempted_at)}
                </span>
                {data.parse_state?.error?.message ? (
                  <span className="mt-1 block text-destructive">
                    {data.parse_state.error.message}
                  </span>
                ) : null}
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell className="text-xs font-medium">语义索引</TableCell>
              <TableCell>
                <StatusBadge status={data.embedding_state?.status ?? "pending"} />
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                <span className="block">
                  已索引 {data.embedding_state?.indexed_count ?? 0} /{" "}
                  {data.embedding_state?.chunk_count ?? 0} 个片段
                </span>
                <span className="block">
                  模型：{data.embedding_state?.model_name ?? "未记录"}
                </span>
                {data.embedding_state?.error?.message ? (
                  <span className="mt-1 block text-destructive">
                    {data.embedding_state.error.message}
                  </span>
                ) : null}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </section>

      <Separator />

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">当前片段</h3>
          <Badge variant="secondary">{data.current_chunks.count} 个</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          仅展示当前成功解析批次；更早的历史片段仍可由旧报告引用。
        </p>
        {data.current_chunks.items.length ? (
          <ol className="divide-y divide-border">
            {data.current_chunks.items.map((chunk) => (
              <li
                key={chunk.chunk_id}
                className="py-3 first:pt-0 last:pb-0"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="font-medium">
                    片段 {chunk.position + 1}
                  </span>
                  <span className="font-code text-muted-foreground">
                    {shortId(chunk.chunk_id)}
                  </span>
                </div>
                <p className="mt-1.5 text-sm leading-7 break-words whitespace-pre-wrap">
                  {chunk.text}
                </p>
                <LocatorDetails locator={chunk.locator} />
                {onReadContext ? (
                  <button
                    type="button"
                    className="mt-2 text-xs font-medium text-primary underline-offset-4 hover:underline"
                    onClick={() => onReadContext(chunk.chunk_id)}
                  >
                    读取相邻片段上下文
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <EmptyPanel
            title="当前没有可读 Chunk"
            description="这表示当前版本尚无成功解析批次，或解析出的正文为空；请结合上方解析状态判断。"
          />
        )}
      </section>

      <Separator />

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">结构化 Fact</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Fact 是已保存的抽取结果，保留数值、单位、观测期和证据定位。
          </p>
        </div>
        {facts.loading ? (
          <LoadingPanel label="正在读取 Fact" />
        ) : facts.error ? (
          <ErrorPanel message={facts.error.message} onRetry={facts.refresh} />
        ) : (
          <FactTable facts={facts.data?.items ?? []} />
        )}
      </section>
    </article>
  );
}
