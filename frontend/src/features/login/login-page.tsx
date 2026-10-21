import { KeyRound, ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError, login } from "@/api/client";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useSessionStore } from "@/auth/session-store";

type LoginLocationState = {
  from?: { pathname?: string; search?: string; hash?: string };
  reason?: string;
};

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as LoginLocationState | null;
  const setAuthenticated = useSessionStore((store) => store.setAuthenticated);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!password) {
      setError("请输入 Owner 密码。");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const result = await login(password);
      setAuthenticated(result.session);
      setPassword("");
      const target = state?.from
        ? `${state.from.pathname ?? "/"}${state.from.search ?? ""}${state.from.hash ?? ""}`
        : "/";
      navigate(target, { replace: true });
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "登录未能完成，请稍后重试。",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-7 text-center">
          <div className="mb-4 inline-flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/15">
            <ShieldCheck aria-hidden="true" className="size-6" />
          </div>
          <p className="text-xs font-medium tracking-[0.18em] text-primary uppercase">
            Stocks Intelligence
          </p>
          <h1 className="font-editorial mt-2 text-4xl font-semibold tracking-tight">
            研究控制台
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            受保护的单 Owner 工作台
          </p>
        </div>
        <Card className="shadow-xl shadow-foreground/5">
          <CardHeader>
            <CardTitle className="text-lg">登录</CardTitle>
            <CardDescription>
              密码由服务端验证；浏览器只保存 HttpOnly 会话 Cookie。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              {state?.reason ? (
                <Alert variant="warning">
                  <AlertTitle>会话已失效</AlertTitle>
                  <AlertDescription>
                    请重新登录后继续。{state.reason}
                  </AlertDescription>
                </Alert>
              ) : null}
              {error ? (
                <Alert variant="destructive">
                  <AlertTitle>无法登录</AlertTitle>
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}
              <div className="space-y-2">
                <label htmlFor="owner-password" className="text-sm font-medium">
                  Owner 密码
                </label>
                <Input
                  id="owner-password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={error ? true : undefined}
                />
              </div>
              <Button type="submit" size="lg" className="w-full" disabled={pending}>
                <KeyRound aria-hidden="true" />
                {pending ? "正在登录…" : "进入控制台"}
              </Button>
            </form>
          </CardContent>
        </Card>
        <p className="mt-5 text-center text-xs leading-relaxed text-muted-foreground">
          源码、证据与报告都可通过本会话访问。请仅在你控制的安全设备上登录。
        </p>
      </div>
    </main>
  );
}
