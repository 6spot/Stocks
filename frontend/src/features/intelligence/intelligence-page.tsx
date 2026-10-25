import {
  ArrowLeft,
  CalendarRange,
  FileSearch,
  Search,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { dashboardApi } from "@/api/dashboard";
import {
  EmptyPanel,
  ErrorPanel,
  LoadingPanel,
} from "@/components/async-state";
import { DocumentDetails } from "@/components/document-details";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useResource } from "@/hooks/use-resource";
import { formatDateTime, formatValue, shortId } from "@/lib/format";
import { gapLabel, statusLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

type SearchMode = "semantic" | "exact";

function parseMode(value: string | null): SearchMode {
  return value === "exact" ? "exact" : "semantic";
}

function dateInputToIso(value: string, end = false): string | null {
  if (!value) {
    return null;
  }
  const date = new Date(`${value}T${end ? "23:59:59" : "00:00:00"}`);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function IntelligencePage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("document");
  const query = params.get("q") ?? "";
  const exact = params.get("exact") ?? "";
  const contextChunk = params.get("chunk");
  const radius = Number.parseInt(params.get("radius") ?? "1", 10);
  const mode = parseMode(params.get("mode"));
  const sourceId = params.get("source") ?? "";
  const [queryDraft, setQueryDraft] = useState(query || exact);
  const [modeDraft, setModeDraft] = useState<SearchMode>(mode);

  useEffect(() => {
    setQueryDraft(query || exact);
    setModeDraft(mode);
  }, [exact, mode, query]);

  const searchResults = useResource(
    (signal) =>
      contextChunk
        ? dashboardApi.search(
            {
              chunkId: contextChunk,
              radius: Number.isFinite(radius) ? radius : 1,
            },
            signal,
          )
        : dashboardApi.search(
            {
              query: mode === "semantic" ? query : null,
              exact: mode === "exact" ? exact : null,
              sourceId: sourceId || null,
              publishedFrom: dateInputToIso(
                params.get("published_from") ?? "",
              ),
              publishedTo: dateInputToIso(
                params.get("published_to") ?? "",
                true,
              ),
              collectedFrom: dateInputToIso(
                params.get("collected_from") ?? "",
              ),
              collectedTo: dateInputToIso(
                params.get("collected_to") ?? "",
                true,
              ),
            },
            signal,
          ),
    [
      contextChunk,
      exact,
      mode,
      params.get("collected_from"),
      params.get("collected_to"),
      params.get("published_from"),
      params.get("published_to"),
      query,
      radius,
      sourceId,
    ],
  );
  const recentDocuments = useResource(
    (signal) => dashboardApi.documents({ limit: 50 }, signal),
    [],
  );
  const sources = useResource(
    (signal) => dashboardApi.sources(signal),
    [],
  );

  function updateParams(updates: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(updates)) {
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
    }
    setParams(next, { replace: true });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = queryDraft.trim();
    if (!value) {
      return;
    }
    updateParams({
      mode: modeDraft,
      q: modeDraft === "semantic" ? value : null,
      exact: modeDraft === "exact" ? value : null,
      chunk: null,
    });
  }

  function showContext(chunkId: string) {
    updateParams({ chunk: chunkId, mode: null, q: null, exact: null });
  }

  function exitContext() {
    updateParams({ chunk: null, q: query || exact || null });
  }

  const hasSearch = Boolean(contextChunk || query || exact);
  const results = hasSearch ? searchResults : recentDocuments;

  return (
    <div className="space-y-6">
      <PageHeader
        title="情报检索"
        description="语义检索默认只读当前已保存片段与索引；精确模式适合股票代码和指标编号。"
      />

      <div className="space-y-3">
        <Tabs
          value={modeDraft}
          onValueChange={(value) => setModeDraft(parseMode(String(value)))}
        >
          <TabsList aria-label="检索方式">
            <TabsTrigger value="semantic">
              <FileSearch aria-hidden="true" />
              语义
            </TabsTrigger>
            <TabsTrigger value="exact">
              <Search aria-hidden="true" />
              精确
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <form className="grid gap-3" onSubmit={submit}>
          <div className="flex flex-col gap-2 sm:flex-row">
            <label htmlFor="intelligence-query" className="sr-only">
              {modeDraft === "semantic" ? "语义检索问题" : "股票代码或指标编号"}
            </label>
            <Input
              id="intelligence-query"
              value={queryDraft}
              onChange={(event) => setQueryDraft(event.target.value)}
              placeholder={
                modeDraft === "semantic"
                  ? "例如：哪些来源下调了数据中心资本开支预期？"
                  : "例如：NVDA、cpi_yoy"
              }
              className="h-10 flex-1"
            />
            <Button type="submit" size="lg">
              <Search aria-hidden="true" />
              检索
            </Button>
          </div>

          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-medium">
              <span className="inline-flex items-center gap-2">
                <CalendarRange aria-hidden="true" className="size-4" />
                来源与时间筛选
              </span>
            </summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <label className="grid gap-1 text-xs font-medium">
                来源
                <select
                  className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
                  value={sourceId}
                  onChange={(event) =>
                    updateParams({ source: event.target.value || null })
                  }
                >
                  <option value="">全部来源</option>
                  {sources.data?.items.map((source) => (
                    <option key={source.source_id} value={source.source_id}>
                      {source.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-xs font-medium">
                发布时间起
                <Input
                  type="date"
                  value={params.get("published_from") ?? ""}
                  onChange={(event) =>
                    updateParams({
                      published_from: event.target.value || null,
                    })
                  }
                />
              </label>
              <label className="grid gap-1 text-xs font-medium">
                发布时间止
                <Input
                  type="date"
                  value={params.get("published_to") ?? ""}
                  onChange={(event) =>
                    updateParams({
                      published_to: event.target.value || null,
                    })
                  }
                />
              </label>
              <label className="grid gap-1 text-xs font-medium">
                采集时间起
                <Input
                  type="date"
                  value={params.get("collected_from") ?? ""}
                  onChange={(event) =>
                    updateParams({
                      collected_from: event.target.value || null,
                    })
                  }
                />
              </label>
              <label className="grid gap-1 text-xs font-medium">
                采集时间止
                <Input
                  type="date"
                  value={params.get("collected_to") ?? ""}
                  onChange={(event) =>
                    updateParams({
                      collected_to: event.target.value || null,
                    })
                  }
                />
              </label>
            </div>
          </details>
        </form>
      </div>

      {contextChunk ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-sky-50 p-3 text-sm text-sky-950">
          <span>当前显示片段 {shortId(contextChunk)} 的相邻上下文。</span>
          <Button type="button" variant="outline" size="sm" onClick={exitContext}>
            <ArrowLeft aria-hidden="true" />
            返回检索结果
          </Button>
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(20rem,0.78fr)_minmax(0,1.22fr)]">
        <section className="min-w-0 space-y-3" aria-label="检索结果">
          {results.loading ? (
            <LoadingPanel label="正在读取检索结果" />
          ) : results.error ? (
            <ErrorPanel
              message={results.error.message}
              onRetry={results.refresh}
            />
          ) : !results.data ? (
            <EmptyPanel title="输入条件开始检索" />
          ) : hasSearch && searchResults.data ? (
            <SearchHits
              data={searchResults.data}
              selectedDocumentId={selectedId}
              onSelectDocument={(documentId) =>
                updateParams({ document: documentId })
              }
            />
          ) : (
            <Documents
              items={recentDocuments.data?.items ?? []}
              selectedId={selectedId}
              onSelect={(documentId) =>
                updateParams({ document: documentId })
              }
            />
          )}
        </section>

        <section className="min-w-0 xl:sticky xl:top-5 xl:self-start">
          {selectedId ? (
            <DocumentDetails
              key={selectedId}
              documentId={selectedId}
              onReadContext={showContext}
            />
          ) : (
            <EmptyPanel
              title="选择一个结果查看完整证据"
              description="详情会分开显示采集版本、解析批次、当前片段、Fact、索引状态和原件。"
            />
          )}
        </section>
      </div>
    </div>
  );
}

function SearchHits({
  data,
  selectedDocumentId,
  onSelectDocument,
}: {
  data: Awaited<ReturnType<typeof dashboardApi.search>>;
  selectedDocumentId: string | null;
  onSelectDocument: (documentId: string) => void;
}) {
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">
          {data.mode === "context"
            ? "上下文片段"
            : data.mode === "exact"
              ? "精确命中"
              : "语义命中"}
          <span className="ml-2 text-muted-foreground">
            {data.hits.length} 个
          </span>
        </p>
        {data.truncated ? <Badge variant="warning">结果已截断</Badge> : null}
      </div>
      {data.coverage ? (
        <p className="text-xs leading-relaxed text-muted-foreground">
          索引覆盖 {data.coverage.indexed_chunks} /{" "}
          {data.coverage.current_chunks} 个片段 ·{" "}
          {data.coverage.documents_without_complete_index} 个 Document
          尚未完整索引
        </p>
      ) : null}
      {data.hits.length ? (
        <div className="divide-y divide-border">
          {data.hits.map((hit) => {
            const selected = hit.document_id === selectedDocumentId;
            return (
              <div
                key={hit.chunk_id}
                className={cn(
                  "press -ml-3 cursor-pointer border-l-2 py-3 pl-3 first:pt-0 last:pb-0",
                  selected
                    ? "border-l-primary bg-muted/40"
                    : "border-l-transparent hover:bg-muted/40",
                )}
              >
                <div
                  role="button"
                  tabIndex={0}
                  aria-label={`查看 ${hit.title} 的 Document 详情`}
                  aria-pressed={selected}
                  className="cursor-pointer"
                  onClick={() => onSelectDocument(hit.document_id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelectDocument(hit.document_id);
                    }
                  }}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="line-clamp-2 min-w-0 flex-1 text-sm font-medium">
                      {hit.title}
                    </h3>
                    {hit.similarity !== null ? (
                      <Badge variant="secondary">
                        相似度 {hit.similarity.toFixed(3)}
                      </Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {hit.source_id} · v{hit.version} · 片段 {hit.position + 1}
                  </p>
                  <p className="mt-2 line-clamp-5 text-sm leading-6 whitespace-pre-wrap">
                    {hit.text}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <StatusBadge status={hit.completeness} />
                    <span>发布 {formatDateTime(hit.published_at)}</span>
                    <span>采集 {formatDateTime(hit.collected_at)}</span>
                  </div>
                </div>
                <details className="mt-2 text-xs text-muted-foreground">
                  <summary className="cursor-pointer font-medium text-foreground">
                    命中定位
                  </summary>
                  <pre className="mt-2 max-h-44 overflow-auto rounded-lg bg-muted p-3 whitespace-pre-wrap">
                    {formatValue(hit.locator)}
                  </pre>
                </details>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyPanel
          title="没有命中已保存片段"
          description="这表示当前查询没有返回结果；请同时检查上方索引覆盖，不能据此推断资料不存在。"
        />
      )}
    </>
  );
}

function Documents({
  items,
  selectedId,
  onSelect,
}: {
  items: Awaited<ReturnType<typeof dashboardApi.documents>>["items"];
  selectedId: string | null;
  onSelect: (documentId: string) => void;
}) {
  if (!items.length) {
    return (
      <EmptyPanel
        title="仓库中还没有 Document"
        description="可以先去任务页执行采集，或检查来源配置。"
      />
    );
  }
  return (
    <div className="divide-y divide-border">
      <div className="flex items-center gap-2 pb-3">
        <p className="text-sm font-medium">最近采集</p>
        <Badge variant="outline">{items.length}</Badge>
      </div>
      {items.map((document) => {
        const selected = selectedId === document.id;
        return (
          <button
            key={document.id}
            type="button"
            aria-pressed={selected}
            onClick={() => onSelect(document.id)}
            className={cn(
              "press -ml-3 w-[calc(100%+1.5rem)] border-l-2 py-3 pl-3 text-left first:pt-0 last:pb-0",
              selected
                ? "border-l-primary bg-muted/40"
                : "border-l-transparent hover:bg-muted/40",
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                {document.title}
              </span>
              <Badge variant="outline">v{document.version}</Badge>
              <StatusBadge status={document.completeness} />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {document.source_id} · 采集 {formatDateTime(document.collected_at)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              解析 {statusLabel(document.parse_state?.status ?? "missing")} ·
              索引 {statusLabel(document.embedding_state?.status ?? "pending")}
              {document.embedding_state?.current
                ? " · 当前批次"
                : " · 非当前批次"}
            </p>
            {document.fetch_error?.message ? (
              <p className="mt-2 text-xs text-destructive">
                {gapLabel(document.fetch_error.code ?? "获取失败")}：{" "}
                {document.fetch_error.message}
              </p>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
