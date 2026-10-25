import type { ProcessingStage } from "@/api/schemas";
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
}: {
  stages: ProcessingStage[];
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>阶段</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>详情</TableHead>
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
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
