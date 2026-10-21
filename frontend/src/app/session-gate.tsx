import { LoaderCircle } from "lucide-react";
import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { ApiError, restoreSession } from "@/api/client";
import { useSessionStore } from "@/auth/session-store";
import { Button } from "@/components/ui/button";

export function SessionGate() {
  const status = useSessionStore((state) => state.status);
  const error = useSessionStore((state) => state.error);
  const setAuthenticated = useSessionStore((state) => state.setAuthenticated);
  const setAnonymous = useSessionStore((state) => state.setAnonymous);
  const setError = useSessionStore((state) => state.setError);

  useEffect(() => {
    if (status !== "idle" && status !== "loading") {
      return;
    }
    const controller = new AbortController();
    restoreSession(controller.signal)
      .then((payload) => setAuthenticated(payload.session))
      .catch((reason: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        if (reason instanceof ApiError && reason.status === 401) {
          setAnonymous();
          return;
        }
        setError(
          reason instanceof Error
            ? reason.message
            : "无法确认 Owner 会话状态。",
        );
      });
    return () => controller.abort();
  }, [
    setAnonymous,
    setAuthenticated,
    setError,
    status,
  ]);

  if (status === "idle" || status === "loading") {
    return (
      <main className="grid min-h-dvh place-items-center bg-background">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          正在恢复会话…
        </div>
      </main>
    );
  }

  if (status === "error") {
    return (
      <main className="grid min-h-dvh place-items-center px-4">
        <div className="max-w-md rounded-xl border border-destructive/20 bg-card p-5 text-center">
          <h1 className="font-medium">无法确认会话状态</h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {error ?? "API 或网络暂不可用。"}
          </p>
          <Button
            type="button"
            className="mt-4"
            onClick={() => {
              useSessionStore.setState({ status: "idle", error: null });
            }}
          >
            重试
          </Button>
        </div>
      </main>
    );
  }

  return <Outlet />;
}

export function AuthenticatedOnly() {
  const location = useLocation();
  const status = useSessionStore((state) => state.status);
  if (status === "anonymous") {
    return (
      <Navigate
        to="/login"
        replace
        state={{
          from: {
            pathname: location.pathname,
            search: location.search,
            hash: location.hash,
          },
        }}
      />
    );
  }

  return <Outlet />;
}

export function PublicOnly() {
  const status = useSessionStore((state) => state.status);
  if (status === "authenticated") {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}
