import { BookOpenText, Layers3 } from "lucide-react";
import { useResource } from "@/hooks/use-resource";
import { dashboardApi } from "@/api/dashboard";
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/async-state";
import { FactList } from "@/components/fact-list";
import { OriginalLinks } from "@/components/original-links";
import { ProcessingStages } from "@/components/processing-stages";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatBytes, formatDateTime, formatValue, shortId } from "@/lib/format";

function LocatorDetails({ locator }: { locator: unknown }) {
  if (locator === null || locator === undefined) {
    return null;
  }
  return (
    <details className="mt-3 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium text-foreground">
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
    <article className="space-y-4" aria-label="Document 详情">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-lg">{document.title}</CardTitle>
            <Badge variant="outline">v{document.version}</Badge>
            <StatusBadge status={document.completeness} />
            {data.is_current_collection_pointer ? (
              <Badge variant="success">当前采集指针</Badge>
            ) : (
              <Badge variant="warning">历史版本</Badge>
            )}
          </div>
          <CardDescription>
            {document.source_id} · {document.content_type ?? "内容类型未知"} ·{" "}
            {formatBytes(document.content_size)}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>处理状态</CardTitle>
          <CardDescription>
            “已采集”不等于“可搜索”。解析、分类、事实抽取和索引分别记录。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ProcessingStages stages={data.processing_stages} />
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-border p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium">解析批次</p>
                <StatusBadge status={data.parse_state?.status ?? "missing"} />
              </div>
              <p className="mt-2 text-muted-foreground">
                当前成功批次：
                <span className="font-code">
                  {shortId(data.parse_state?.current_batch_id ?? "无")}
                </span>
              </p>
              <p className="text-muted-foreground">
                最新尝试批次：
                <span className="font-code">
                  {shortId(data.parse_state?.latest_batch_id ?? "无")}
                </span>
              </p>
              <p className="text-muted-foreground">
                尝试时间：{formatDateTime(data.parse_state?.attempted_at)}
              </p>
              {data.parse_state?.error?.message ? (
                <p className="mt-2 text-destructive">
                  {data.parse_state.error.message}
                </p>
              ) : null}
            </div>
            <div className="rounded-lg border border-border p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium">语义索引</p>
                <StatusBadge
                  status={data.embedding_state?.status ?? "pending"}
                />
              </div>
              <p className="mt-2 text-muted-foreground">
                已索引 {data.embedding_state?.indexed_count ?? 0} /{" "}
                {data.embedding_state?.chunk_count ?? 0} 个片段
              </p>
              <p className="text-muted-foreground">
                模型：{data.embedding_state?.model_name ?? "未记录"}
              </p>
              {data.embedding_state?.error?.message ? (
                <p className="mt-2 text-destructive">
                  {data.embedding_state.error.message}
                </p>
              ) : null}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Layers3 aria-hidden="true" className="size-4 text-primary" />
            <CardTitle>当前可读片段</CardTitle>
            <Badge variant="secondary">
              {data.current_chunks.count} 个
            </Badge>
          </div>
          <CardDescription>
            仅展示当前成功解析批次；更早的历史片段仍可由旧报告引用。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {data.current_chunks.items.length ? (
            <div className="space-y-3">
              {data.current_chunks.items.map((chunk) => (
                <div
                  key={chunk.chunk_id}
                  className="rounded-xl border border-border bg-background/70 p-3"
                >
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="font-medium">片段 {chunk.position + 1}</span>
                    <span className="font-code text-muted-foreground">
                      {shortId(chunk.chunk_id)}
                    </span>
                  </div>
                  <p className="text-sm leading-7 break-words whitespace-pre-wrap">
                    {chunk.text}
                  </p>
                  <LocatorDetails locator={chunk.locator} />
                  {onReadContext ? (
                    <button
                      type="button"
                      className="mt-3 text-xs font-medium text-primary underline-offset-4 hover:underline"
                      onClick={() => onReadContext(chunk.chunk_id)}
                    >
                      读取相邻片段上下文
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <EmptyPanel
              title="当前没有可读 Chunk"
              description="这表示当前版本尚无成功解析批次，或解析出的正文为空；请结合上方解析状态判断。"
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <BookOpenText aria-hidden="true" className="size-4 text-primary" />
            <CardTitle>结构化 Fact</CardTitle>
          </div>
          <CardDescription>
            Fact 是已保存的抽取结果，保留数值、单位、观测期和证据定位。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {facts.loading ? (
            <LoadingPanel label="正在读取 Fact" />
          ) : facts.error ? (
            <ErrorPanel message={facts.error.message} onRetry={facts.refresh} />
          ) : (
            <FactList facts={facts.data?.items ?? []} />
          )}
        </CardContent>
      </Card>
    </article>
  );
}
