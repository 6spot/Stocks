import { useId, type ReactNode } from "react";
import type {
  ReportDetail,
  ReportEvidence,
} from "@/api/schemas";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { renderMarkdown } from "@/lib/markdown";
import { referenceKey, uniqueEvidenceReferences } from "./research-evidence";

type Analysis = ReportDetail["analysis"];
type Finding = Analysis["findings"][number];

const findingKindLabels: Record<Finding["kind"], string> = {
  fact: "事实",
  source_opinion: "来源观点",
  model_inference: "模型推断",
};

function Markdown({ children }: { children: string }) {
  return (
    <div
      className="report-prose max-w-none"
      dangerouslySetInnerHTML={{ __html: renderMarkdown(children) }}
    />
  );
}

export function ResearchArticle({
  analysis,
  evidence,
  renderEvidence,
}: {
  analysis: Analysis;
  evidence: ReportEvidence[];
  renderEvidence: (evidence: ReportEvidence, index: number) => ReactNode;
}) {
  const articleId = useId();
  const findingById = new Map(analysis.findings.map((finding) => [finding.id, finding]));
  const evidenceByRef = new Map(
    uniqueEvidenceReferences(evidence).map((item) => [referenceKey(item), item]),
  );
  // Frozen input order first, then saved references absent from that input.
  // Disclosure state never changes numbers.
  const references = uniqueEvidenceReferences([
    ...evidence,
    ...analysis.evidence_refs,
    ...analysis.findings.flatMap((finding) => finding.evidence_refs),
  ]);

  function citations(findings: Finding[]) {
    const keys = new Set(
      findings.flatMap((finding) => finding.evidence_refs.map(referenceKey)),
    );
    return references.flatMap((ref, index) => {
      const key = referenceKey(ref);
      if (!keys.has(key)) return [];
      const item = evidenceByRef.get(key);
      const number = index + 1;
      return [
        <Dialog key={key}>
          <DialogTrigger
            aria-label={`查看来源 ${number}：${item?.document.title ?? "引用片段不可用"}`}
            className="inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center rounded-sm px-1 text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:inline sm:min-h-0 sm:min-w-0"
          >
            [E{number}]
          </DialogTrigger>
          <DialogContent className="max-w-3xl">
            <DialogHeader>
              <DialogTitle>来源 [E{number}]</DialogTitle>
              <DialogDescription>
                报告保存时使用的证据片段与版本；核对摘录后可查看原件。
              </DialogDescription>
            </DialogHeader>
            {item ? (
              renderEvidence(item, index)
            ) : (
              <p className="text-sm text-muted-foreground">
                这条引用的保存片段当前无法读取，不能据此判断原始资料不存在。
              </p>
            )}
          </DialogContent>
        </Dialog>,
      ];
    });
  }

  return (
    <div className="space-y-8">
      {analysis.summary ? <Markdown>{analysis.summary}</Markdown> : null}
      {analysis.sections.map((section, index) => {
        const findings = section.finding_ids.flatMap((id) => {
          const finding = findingById.get(id);
          return finding ? [finding] : [];
        });
        const headingId = `${articleId}-section-${index}`;
        const sectionCitations = citations(findings);
        return (
          <section key={headingId} aria-labelledby={headingId} className="space-y-4">
            <h2 id={headingId} className="text-lg font-semibold tracking-tight">
              {section.heading}
            </h2>
            <Markdown>{section.body}</Markdown>
            {sectionCitations.length ? (
              <div className="flex flex-wrap items-center gap-1" aria-label="章节来源">
                <span className="mr-1 text-xs text-muted-foreground">来源</span>
                {sectionCitations}
              </div>
            ) : null}
            {findings.length ? (
              <details className="group border-l-2 border-border pl-4">
                <summary className="min-h-11 w-fit cursor-pointer rounded-sm py-3 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-0 sm:py-0">
                  查看分析依据 · {findings.length} 项发现
                </summary>
                <div className="mt-4 divide-y divide-border">
                  {findings.map((finding) => (
                    <section key={finding.id} className="space-y-3 py-4 first:pt-0 last:pb-0">
                      <div className="space-y-1">
                        <span className="text-xs text-muted-foreground">
                          {findingKindLabels[finding.kind]}
                        </span>
                        <h3 className="text-sm font-medium">{finding.title}</h3>
                      </div>
                      <Markdown>{finding.statement}</Markdown>
                      <div className="space-y-1 text-sm text-muted-foreground">
                        <p className="text-xs font-medium">分析依据</p>
                        <Markdown>{finding.rationale}</Markdown>
                      </div>
                      <div className="space-y-1 text-sm text-muted-foreground">
                        <p className="text-xs font-medium">判断边界</p>
                        <Markdown>{finding.uncertainty}</Markdown>
                      </div>
                      <div className="flex flex-wrap items-center gap-1" aria-label="发现来源">
                        <span className="mr-1 text-xs text-muted-foreground">来源</span>
                        {citations([finding])}
                      </div>
                    </section>
                  ))}
                </div>
              </details>
            ) : null}
          </section>
        );
      })}
      {analysis.open_questions.length ? (
        <section className="space-y-3" aria-labelledby={`${articleId}-questions`}>
          <h2 id={`${articleId}-questions`} className="text-lg font-semibold tracking-tight">
            待解问题
          </h2>
          <ul className="space-y-2 pl-5 text-sm leading-relaxed marker:text-muted-foreground list-disc">
            {analysis.open_questions.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
