import type { SafeError } from "@/api/schemas";

export function errorText(
  error: Error | SafeError | null | undefined,
): string {
  if (!error) {
    return "未提供错误详情";
  }
  if ("message" in error) {
    if (error instanceof Error) {
      return error.message || "请求未能完成";
    }
    return error.message ?? error.code ?? "请求未能完成";
  }
  return "请求未能完成";
}

export function safeErrorText(
  error: SafeError,
  fallback = "没有安全错误详情",
): string {
  if (!error) {
    return fallback;
  }
  return error.message ?? error.code ?? fallback;
}
