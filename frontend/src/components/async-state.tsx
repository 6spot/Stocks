import { AlertCircle, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export function LoadingPanel({ label = "正在读取数据" }: { label?: string }) {
  return (
    <div
      className="grid gap-3 p-1 motion-rise"
      aria-busy="true"
      aria-label={label}
    >
      <Skeleton className="h-5 w-2/5" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-4/5" />
    </div>
  );
}

export function ErrorPanel({
  title = "无法读取数据",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <Alert variant="destructive">
      <AlertCircle aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col items-start gap-3">
        <span>{message}</span>
        {onRetry ? (
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
            重试
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

export function EmptyPanel({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center rounded-lg border border-dashed border-border px-5 py-8 text-center">
      <Inbox
        aria-hidden="true"
        className="mb-3 size-6 text-muted-foreground/70"
      />
      <p className="font-medium">{title}</p>
      {description ? (
        <p className="mt-1 max-w-lg text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function InlineError({
  message,
  details,
}: {
  message: string;
  details?: string[];
}) {
  return (
    <Alert variant="destructive">
      <AlertCircle aria-hidden="true" />
      <AlertTitle>{message}</AlertTitle>
      {details?.length ? (
        <AlertDescription>
          <ul className="list-disc space-y-1 pl-4">
            {details.map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        </AlertDescription>
      ) : null}
    </Alert>
  );
}
