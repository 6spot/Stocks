import {
  Activity,
  BookOpenText,
  ClipboardList,
  Database,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  X,
} from "lucide-react";
import { useEffect } from "react";
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
  { to: "/intelligence", label: "情报", icon: Search },
  { to: "/tasks", label: "任务", icon: ClipboardList },
  { to: "/research", label: "研究", icon: BookOpenText },
  { to: "/runs", label: "运行", icon: Activity },
];

function NavigationLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="主导航" className="space-y-1">
      {navigation.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "press flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              isActive &&
                "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm",
            )
          }
        >
          <Icon aria-hidden="true" className="size-4" />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-3">
      <div className="grid size-9 place-items-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground">
        <Database aria-hidden="true" className="size-4" />
      </div>
      <div>
        <p className="font-editorial text-lg leading-none font-semibold">
          Stocks
        </p>
        <p className="mt-1 text-[0.68rem] tracking-[0.12em] text-sidebar-foreground/50 uppercase">
          Research desk
        </p>
      </div>
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

  useEffect(() => {
    setOpen(false);
  }, [location.pathname, setOpen]);

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
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="hidden min-h-dvh flex-col bg-sidebar p-4 text-sidebar-foreground lg:sticky lg:top-0 lg:flex lg:h-dvh">
        <Brand />
        <div className="mt-8 flex-1">
          <p className="mb-2 px-3 text-[0.66rem] font-medium tracking-[0.15em] text-sidebar-foreground/40 uppercase">
            Workspace
          </p>
          <NavigationLinks />
        </div>
        <div className="border-t border-sidebar-border pt-3">
          <Button
            type="button"
            variant="ghost"
            className="w-full justify-start text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            onClick={signOut}
          >
            <LogOut aria-hidden="true" />
            退出登录
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
