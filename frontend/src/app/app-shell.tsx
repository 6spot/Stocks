import {
  Activity,
  BookOpenText,
  ClipboardList,
  Database,
  LayoutDashboard,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Rss,
  Search,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { logout } from "@/api/client";
import { useSessionStore } from "@/auth/session-store";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useMobileNavigationStore, useNoticeStore } from "@/state/ui-store";

const navigation = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/sources", label: "采集源", icon: Rss },
  { to: "/intelligence", label: "情报", icon: Search },
  { to: "/tasks", label: "任务", icon: ClipboardList },
  { to: "/research", label: "研究", icon: BookOpenText },
  { to: "/runs", label: "运行", icon: Activity },
];

const SIDEBAR_COLLAPSED_KEY = "stocks.sidebar-collapsed";

function NavigationLinks({
  collapsed = false,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <nav aria-label="主导航" className="space-y-1">
      {navigation.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          title={label}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "press flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              isActive &&
                "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm",
              collapsed && "justify-center gap-0 px-2",
            )
          }
        >
          <Icon aria-hidden="true" className="size-4 shrink-0" />
          <span className={cn(collapsed && "sr-only")}>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

function Brand({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground">
        <Database aria-hidden="true" className="size-4" />
      </div>
      {collapsed ? null : (
        <div>
          <p className="text-lg leading-none font-semibold">Stocks</p>
          <p className="mt-1 text-[0.68rem] text-sidebar-foreground/50">
            Research desk
          </p>
        </div>
      )}
    </div>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const setAnonymous = useSessionStore((state) => state.setAnonymous);
  const notify = useNoticeStore((state) => state.notify);
  const open = useMobileNavigationStore((state) => state.open);
  const setOpen = useMobileNavigationStore((state) => state.setOpen);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1",
  );

  useEffect(() => {
    setOpen(false);
  }, [location.pathname, setOpen]);

  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  async function signOut() {
    try {
      await logout();
    } catch {
      setAnonymous();
    }
    notify("已退出 Owner 会话。", "info");
    navigate("/login", { replace: true });
  }

  return (
    <div
      className={cn(
        "min-h-dvh lg:grid",
        collapsed
          ? "lg:grid-cols-[4rem_minmax(0,1fr)]"
          : "lg:grid-cols-[15rem_minmax(0,1fr)]",
      )}
    >
      <aside
        className={cn(
          "hidden min-h-dvh flex-col bg-sidebar p-4 text-sidebar-foreground lg:sticky lg:top-0 lg:flex lg:h-dvh",
          collapsed && "px-2",
        )}
      >
        <div className="flex items-center justify-between gap-2">
          <Brand collapsed={collapsed} />
          {collapsed ? null : (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="折叠侧栏"
              title="折叠侧栏"
              className="shrink-0 text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={() => setCollapsed(true)}
            >
              <PanelLeftClose aria-hidden="true" className="size-4" />
            </Button>
          )}
        </div>
        {collapsed ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="展开侧栏"
            title="展开侧栏"
            className="mt-3 self-center text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            onClick={() => setCollapsed(false)}
          >
            <PanelLeftOpen aria-hidden="true" className="size-4" />
          </Button>
        ) : null}
        <div className={cn("flex-1", collapsed ? "mt-3" : "mt-8")}>
          {collapsed ? null : (
            <p className="mb-2 px-3 text-[0.66rem] font-medium text-sidebar-foreground/40">
              Workspace
            </p>
          )}
          <NavigationLinks collapsed={collapsed} />
        </div>
        <div className="border-t border-sidebar-border pt-3">
          <Button
            type="button"
            variant="ghost"
            title="退出登录"
            aria-label={collapsed ? "退出登录" : undefined}
            className={cn(
              "w-full justify-start text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              collapsed && "justify-center",
            )}
            onClick={signOut}
          >
            <LogOut aria-hidden="true" className="shrink-0" />
            {collapsed ? null : "退出登录"}
          </Button>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border/80 bg-background/90 px-4 backdrop-blur lg:hidden">
          <Brand />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="打开导航"
            onClick={() => setOpen(true)}
          >
            <Menu aria-hidden="true" />
          </Button>
        </header>
        <main className="mx-auto w-full max-w-[92rem] px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
          <div key={location.pathname} className="motion-rise">
            <Outlet />
          </div>
        </main>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="inset-x-3 top-3 left-3 h-[calc(100dvh-1.5rem)] w-[min(20rem,calc(100%-1.5rem))] max-w-none translate-none bg-sidebar p-4 text-sidebar-foreground"
          showCloseButton={false}
        >
          <DialogHeader>
            <div className="flex items-center justify-between">
              <Brand />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="关闭导航"
                className="text-sidebar-foreground"
                onClick={() => setOpen(false)}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
            <DialogTitle className="sr-only">主导航</DialogTitle>
            <DialogDescription className="sr-only">
              选择控制台页面
            </DialogDescription>
          </DialogHeader>
          <NavigationLinks onNavigate={() => setOpen(false)} />
          <div className="mt-auto border-t border-sidebar-border pt-3">
            <Button
              type="button"
              variant="ghost"
              className="w-full justify-start text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={() => {
                setOpen(false);
                void signOut();
              }}
            >
              <LogOut aria-hidden="true" />
              退出登录
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
