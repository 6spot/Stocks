import { RotateCcw } from "lucide-react";
import type { ProcessingStage } from "@/api/schemas";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/status-badge";
import { stageLabel } from "@/lib/labels";

export function ProcessingStages({
  stages,
  onRetry,
  retryPendingStage = null,
}: {
  stages: ProcessingStage[];
  onRetry?: (stage: string) => void;
  retryPendingStage?: string | null;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>阶段</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>详情</TableHead>
          {onRetry ? <TableHead className="w-16">操作</TableHead> : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {stages.map((stage) => (
          <TableRow key={stage.stage}>
            <TableCell className="text-xs font-medium">
              {stageLabel(stage.stage)}
            </TableCell>
            <TableCell>
              <StatusBadge status={stage.status} />
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              尝试 {stage.attempts} 次
              {stage.wait_reason
                ? ` · 等待：${stage.wait_reason.replaceAll("_", " ")}`
                : ""}
              {stage.skip_reason ? (
                <span className="block">
                  跳过原因：{stage.skip_reason.replaceAll("_", " ")}
                </span>
              ) : null}
              {stage.error?.message || stage.error?.code ? (
                <span className="mt-1 block leading-relaxed text-destructive">
                  {stage.error.message ?? stage.error.code}
                </span>
              ) : null}
            </TableCell>
            {onRetry ? (
              <TableCell>
                {stage.status === "failed" ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={retryPendingStage !== null}
                    onClick={() => onRetry(stage.stage)}
                  >
                    <RotateCcw aria-hidden="true" />
                    {retryPendingStage === stage.stage ? "排队中…" : "重试"}
                  </Button>
                ) : null}
              </TableCell>
            ) : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
