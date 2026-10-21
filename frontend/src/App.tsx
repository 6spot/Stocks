import { MotionConfig } from "motion/react";
import { BrowserRouter } from "react-router-dom";
import { AppErrorBoundary } from "@/components/app-error-boundary";
import { Notices } from "@/components/notices";
import { AppRouter } from "@/app/router";

export default function App() {
  return (
    <AppErrorBoundary>
      <MotionConfig reducedMotion="user">
        <BrowserRouter>
          <AppRouter />
          <Notices />
        </BrowserRouter>
      </MotionConfig>
    </AppErrorBoundary>
  );
}
