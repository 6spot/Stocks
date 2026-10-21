import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorPanel } from "@/components/async-state";

export class AppErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // The production boundary deliberately does not render raw client errors.
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="mx-auto grid min-h-dvh max-w-2xl place-items-center p-6">
          <ErrorPanel
            title="控制台遇到异常"
            message="页面没有继续渲染。请刷新后重试；已保存的报告和证据不会被修改。"
            onRetry={() => window.location.reload()}
          />
        </main>
      );
    }
    return this.props.children;
  }
}
