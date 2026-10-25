import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useNoticeStore } from "@/state/ui-store";

export function Notices() {
  const notices = useNoticeStore((state) => state.notices);
  const dismiss = useNoticeStore((state) => state.dismiss);

  return (
    <div
      className="pointer-events-none fixed inset-x-3 bottom-3 z-[70] flex flex-col items-end gap-2 sm:left-auto sm:w-96"
      aria-live="polite"
      aria-atomic="false"
    >
      {notices.map((notice) => {
        const Icon =
          notice.tone === "success"
            ? CircleCheck
            : notice.tone === "error"
              ? CircleAlert
              : Info;
        return (
          <div
            key={notice.id}
            className={cn(
              "motion-rise pointer-events-auto flex w-full items-start gap-2 rounded-xl border bg-popover px-3 py-2.5 text-sm shadow-lg shadow-foreground/5",
              notice.tone === "success" &&
                "border-emerald-200 text-emerald-950",
              notice.tone === "error" &&
                "border-destructive/30 text-destructive",
              notice.tone === "info" && "border-sky-200 text-sky-950",
            )}
          >
            <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 flex-1 leading-relaxed">
              {notice.message}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="关闭通知"
              onClick={() => dismiss(notice.id)}
            >
              <X aria-hidden="true" />
            </Button>
          </div>
        );
      })}
    </div>
  );
}
