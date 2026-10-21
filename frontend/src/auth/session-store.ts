import { create } from "zustand";
import type { SessionPayload } from "@/api/schemas";

type Session = SessionPayload["session"];
type SessionStatus =
  | "idle"
  | "loading"
  | "authenticated"
  | "anonymous"
  | "error";

type SessionState = {
  status: SessionStatus;
  session: Session | null;
  error: string | null;
  setAuthenticated: (session: Session) => void;
  setAnonymous: () => void;
  setError: (message: string) => void;
};

export const useSessionStore = create<SessionState>()((set) => ({
  status: "idle",
  session: null,
  error: null,
  setAuthenticated: (session) =>
    set({ status: "authenticated", session, error: null }),
  setAnonymous: () => set({ status: "anonymous", session: null, error: null }),
  setError: (message) =>
    set({ status: "error", session: null, error: message }),
}));
