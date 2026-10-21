import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "@/app/app-shell";
import {
  AuthenticatedOnly,
  PublicOnly,
  SessionGate,
} from "@/app/session-gate";
import { NotFoundPage } from "@/app/not-found-page";
import { LoadingPanel } from "@/components/async-state";

const LoginPage = lazy(() =>
  import("@/features/login/login-page").then((module) => ({
    default: module.LoginPage,
  })),
);
const OverviewPage = lazy(() =>
  import("@/features/overview/overview-page").then((module) => ({
    default: module.OverviewPage,
  })),
);
const IntelligencePage = lazy(() =>
  import("@/features/intelligence/intelligence-page").then((module) => ({
    default: module.IntelligencePage,
  })),
);
const TasksPage = lazy(() =>
  import("@/features/tasks/tasks-page").then((module) => ({
    default: module.TasksPage,
  })),
);
const ResearchPage = lazy(() =>
  import("@/features/research/research-page").then((module) => ({
    default: module.ResearchPage,
  })),
);
const RunsPage = lazy(() =>
  import("@/features/runs/runs-page").then((module) => ({
    default: module.RunsPage,
  })),
);

function RouteFallback() {
  return (
    <div className="mx-auto max-w-4xl py-10">
      <LoadingPanel label="正在加载页面" />
    </div>
  );
}

export function AppRouter() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route element={<SessionGate />}>
          <Route element={<PublicOnly />}>
            <Route path="/login" element={<LoginPage />} />
          </Route>
          <Route element={<AuthenticatedOnly />}>
            <Route element={<AppShell />}>
              <Route index element={<OverviewPage />} />
              <Route path="/intelligence" element={<IntelligencePage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/research" element={<ResearchPage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Route>
        </Route>
      </Routes>
    </Suspense>
  );
}
