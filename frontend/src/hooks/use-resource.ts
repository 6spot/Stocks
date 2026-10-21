import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";

type ResourceState<T> = {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  refresh: () => void;
};

export function useResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  dependencies: readonly unknown[],
): ResourceState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError(null);

    loader(controller.signal)
      .then((value) => {
        if (currentRequest !== requestId.current) {
          return;
        }
        setData(value);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (
          controller.signal.aborted ||
          (reason instanceof DOMException && reason.name === "AbortError")
        ) {
          return;
        }
        if (currentRequest !== requestId.current) {
          return;
        }
        setError(
          reason instanceof Error
            ? reason
            : new Error("请求未能完成"),
        );
        setLoading(false);
      });

    return () => controller.abort();
    // The caller owns the dependency list, matching the useResource contract.
  }, [...dependencies, revision]);

  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return { data, error, loading, refresh };
}
