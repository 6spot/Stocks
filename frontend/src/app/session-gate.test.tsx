import { render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AuthenticatedOnly,
  PublicOnly,
  SessionGate,
} from "@/app/session-gate";
import { useSessionStore } from "@/auth/session-store";

const { restoreSessionMock } = vi.hoisted(() => ({
  restoreSessionMock: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  restoreSession: restoreSessionMock,
}));

describe("SessionGate", () => {
  beforeEach(() => {
    restoreSessionMock.mockReset();
    useSessionStore.setState({
      status: "idle",
      session: null,
      error: null,
    });
  });

  it("finishes session restoration when StrictMode replays the effect", async () => {
    restoreSessionMock.mockResolvedValue({
      authenticated: true,
      session: {
        issued_at: "2026-10-20T00:00:00Z",
        expires_at: "2026-10-21T00:00:00Z",
        csrf_token: "fixture-csrf",
      },
    });

    render(
      <StrictMode>
        <MemoryRouter>
          <Routes>
            <Route element={<SessionGate />}>
              <Route element={<AuthenticatedOnly />}>
                <Route path="/" element={<h1>Protected content</h1>} />
              </Route>
            </Route>
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );

    expect(
      await screen.findByRole("heading", { name: "Protected content" }),
    ).toBeVisible();
    expect(
      useSessionStore.getState().status,
    ).toBe("authenticated");
  });

  it("restores an existing session before rendering the public login route", async () => {
    restoreSessionMock.mockResolvedValue({
      authenticated: true,
      session: {
        issued_at: "2026-10-20T00:00:00Z",
        expires_at: "2026-10-21T00:00:00Z",
        csrf_token: "fixture-csrf",
      },
    });

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route element={<SessionGate />}>
            <Route element={<PublicOnly />}>
              <Route path="/login" element={<h1>Login form</h1>} />
            </Route>
            <Route path="/" element={<h1>Overview</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeVisible();
    expect(restoreSessionMock).toHaveBeenCalledTimes(1);
  });
});
