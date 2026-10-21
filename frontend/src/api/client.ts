import { z } from "zod";
import {
  apiErrorSchema,
  healthSchema,
  overviewSchema,
  sessionPayloadSchema,
  type Overview,
} from "@/api/schemas";
import { useSessionStore } from "@/auth/session-store";

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | null;
  readonly network: boolean;

  constructor(
    message: string,
    options: {
      status?: number;
      code?: string;
      retryAfter?: number | null;
      network?: boolean;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? 0;
    this.code = options.code ?? "API_REQUEST_FAILED";
    this.retryAfter = options.retryAfter ?? null;
    this.network = options.network ?? false;
  }
}

export type ApiRequestOptions<T> = {
  schema: z.ZodType<T>;
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  allowUnauthorized?: boolean;
};

function contentMessage(payload: unknown): { code: string; message: string } {
  const parsed = apiErrorSchema.safeParse(payload);
  if (parsed.success) {
    if (typeof parsed.data.detail === "string" && parsed.data.detail.trim()) {
      return {
        code: parsed.data.code ?? "API_REQUEST_FAILED",
        message: parsed.data.detail.trim(),
      };
    }
    if (
      parsed.data.detail &&
      typeof parsed.data.detail === "object"
    ) {
      return {
        code:
          parsed.data.detail.code ??
          parsed.data.code ??
          "API_REQUEST_FAILED",
        message:
          parsed.data.detail.message ??
          parsed.data.message ??
          "请求未能完成",
      };
    }
    return {
      code: parsed.data.code ?? "API_REQUEST_FAILED",
      message: parsed.data.message ?? "请求未能完成",
    };
  }
  if (typeof payload === "string" && payload.trim()) {
    return { code: "API_REQUEST_FAILED", message: payload.trim() };
  }
  return { code: "API_REQUEST_FAILED", message: "请求未能完成" };
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions<T>,
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers({ Accept: "application/json" });
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (MUTATING_METHODS.has(method)) {
    const csrf = useSessionStore.getState().session?.csrf_token;
    if (csrf) {
      headers.set("X-CSRF-Token", csrf);
    }
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      credentials: "include",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError("无法连接 API，请检查服务状态和网络连接。", {
      network: true,
    });
  }

  const contentType = response.headers.get("content-type") ?? "";
  let payload: unknown;
  try {
    payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
  } catch {
    throw new ApiError("API 返回了无法读取的响应。", {
      status: response.status,
      code: "API_RESPONSE_INVALID",
    });
  }

  if (!response.ok) {
    if (response.status === 401 && !options.allowUnauthorized) {
      useSessionStore.getState().setAnonymous();
    }
    const detail = contentMessage(payload);
    const retryAfter = Number.parseInt(
      response.headers.get("retry-after") ?? "",
      10,
    );
    throw new ApiError(detail.message, {
      status: response.status,
      code: detail.code,
      retryAfter: Number.isFinite(retryAfter) ? retryAfter : null,
    });
  }

  const parsed = options.schema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError("API 响应与当前控制台版本不兼容。", {
      status: response.status,
      code: "API_RESPONSE_INVALID",
    });
  }
  return parsed.data;
}

export async function getHealth(signal?: AbortSignal) {
  return apiRequest("/health", {
    schema: healthSchema,
    signal,
    allowUnauthorized: true,
  });
}

export async function restoreSession(signal?: AbortSignal) {
  return apiRequest("/api/session", {
    schema: sessionPayloadSchema,
    signal,
    allowUnauthorized: true,
  });
}

export async function login(password: string, signal?: AbortSignal) {
  return apiRequest("/api/session/login", {
    method: "POST",
    body: { password },
    schema: sessionPayloadSchema,
    signal,
    allowUnauthorized: true,
  });
}

export async function logout() {
  const result = await apiRequest("/api/session/logout", {
    method: "POST",
    schema: z.object({ authenticated: z.literal(false) }).passthrough(),
  });
  useSessionStore.getState().setAnonymous();
  return result;
}

export async function getOverview(signal?: AbortSignal): Promise<Overview> {
  return apiRequest("/api/overview", { schema: overviewSchema, signal });
}
