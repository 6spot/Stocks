import { AlertTriangle } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function ConfirmStopDialog({
  open,
  title,
  description,
  confirmLabel,
  pending = false,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const checkboxId = useId();
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    if (open) {
      setConfirmed(false);
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <Alert variant="warning">
          <AlertTriangle aria-hidden="true" />
          <AlertDescription>
            执行系统无法证明旧进程已经退出。只有确认原进程确实停止后，才可释放阶段或结束 Run。
          </AlertDescription>
        </Alert>
        <div className="flex items-start gap-3 rounded-lg border border-border p-3">
          <Checkbox
            id={checkboxId}
            checked={confirmed}
            onCheckedChange={(checked) => setConfirmed(checked === true)}
          />
          <label htmlFor={checkboxId} className="text-sm leading-relaxed">
            我已确认旧进程已停止，不存在并行写入
          </label>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            取消
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={!confirmed || pending}
            onClick={onConfirm}
          >
            {pending ? "处理中…" : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
