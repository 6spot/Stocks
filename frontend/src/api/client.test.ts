import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiRequest } from "@/api/client";
import { useSessionStore } from "@/auth/session-store";
import { z } from "zod";

const session = {
  issued_at: "2026-10-22T00:00:00Z",
  expires_at: "2026-10-22T01:00:00Z",
  csrf_token: "csrf-test-token",
};

describe("apiRequest", () => {
  beforeEach(() => {
    useSessionStore.getState().setAuthenticated(session);
    vi.unstubAllGlobals();
  });

  it("adds the in-memory CSRF token to a mutating request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ accepted: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/action", {
      method: "POST",
      body: { value: 1 },
      schema: z.object({ accepted: z.boolean() }),
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test-token");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.credentials).toBe("include");
  });

  it("clears the in-memory session on a private 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: { code: "AUTH_REQUIRED", message: "Login required" },
          }),
          {
            status: 401,
            headers: { "content-type": "application/json" },
          },
        ),
      ),
    );

    await expect(
      apiRequest("/api/private", { schema: z.object({ ok: z.boolean() }) }),
    ).rejects.toMatchObject({
      status: 401,
      code: "AUTH_REQUIRED",
    });

    expect(useSessionStore.getState().status).toBe("anonymous");
    expect(useSessionStore.getState().session).toBeNull();
  });

  it("keeps login failures anonymous when they return 401", async () => {
    useSessionStore.getState().setAnonymous();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "OWNER_CREDENTIALS_INVALID",
              message: "The Owner credentials are invalid",
            },
          }),
          {
            status: 401,
            headers: { "content-type": "application/json" },
          },
        ),
      ),
    );

    await expect(
      apiRequest("/api/session/login", {
        method: "POST",
        body: { password: "incorrect" },
        schema: z.object({ ok: z.boolean() }),
        allowUnauthorized: true,
      }),
    ).rejects.toMatchObject({
      status: 401,
      code: "OWNER_CREDENTIALS_INVALID",
      message: "The Owner credentials are invalid",
    });
    expect(useSessionStore.getState().status).toBe("anonymous");
  });
});
