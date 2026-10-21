import { Badge } from "@/components/ui/badge";
import { statusLabel, statusTone } from "@/lib/labels";

export function StatusBadge({
  status,
  className,
}: {
  status: string | null | undefined;
  className?: string;
}) {
  return (
    <Badge
      variant={statusTone(status)}
      className={className}
      title={status ?? undefined}
    >
      {statusLabel(status)}
    </Badge>
  );
}
