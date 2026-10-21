import type { ProcessingStage } from "@/api/schemas";
import { StatusBadge } from "@/components/status-badge";
import { stageLabel } from "@/lib/labels";

export function ProcessingStages({
  stages,
}: {
  stages: ProcessingStage[];
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {stages.map((stage) => (
        <div
          key={stage.stage}
          className="rounded-lg border border-border bg-background/70 p-3"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium">{stageLabel(stage.stage)}</p>
            <StatusBadge status={stage.status} />
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            尝试 {stage.attempts} 次
            {stage.wait_reason
              ? ` · 等待：${stage.wait_reason.replaceAll("_", " ")}`
              : ""}
          </p>
          {stage.skip_reason ? (
            <p className="mt-1 text-xs text-muted-foreground">
              跳过原因：{stage.skip_reason.replaceAll("_", " ")}
            </p>
          ) : null}
          {stage.error?.message || stage.error?.code ? (
            <p className="mt-2 text-xs leading-relaxed text-destructive">
              {stage.error.message ?? stage.error.code}
            </p>
          ) : null}
        </div>
      ))}
    </div>
  );
}
