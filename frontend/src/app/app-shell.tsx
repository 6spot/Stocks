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
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
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
const SIDEBAR_WIDTH = "15rem";
const SIDEBAR_RAIL_WIDTH = "4rem";

/**
 * The sidebar animates its own width; every icon sits in a fixed 2rem slot that
 * never moves, so a toggle only slides the right edge and cross-fades labels.
 * Centring the collapsed rail instead would shift each icon horizontally mid
 * transition, and animating the grid track would reflow both panes per frame.
 */
function IconSlot({ children }: { children: ReactNode }) {
  return (
    <span className="grid w-8 shrink-0 place-items-center">{children}</span>
  );
}

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
              "press flex min-h-10 items-center rounded-lg text-sm font-medium text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              isActive &&
                "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm",
            )
          }
        >
          <IconSlot>
            <Icon aria-hidden="true" className="size-4" />
          </IconSlot>
          <span
            className={cn(
              "truncate text-left transition-opacity duration-150",
              collapsed ? "opacity-0" : "opacity-100",
            )}
          >
            {label}
          </span>
        </NavLink>
      ))}
    </nav>
  );
}

function Brand({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center">
      <IconSlot>
        <span className="grid size-8 place-items-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground">
          <Database aria-hidden="true" className="size-4" />
        </span>
      </IconSlot>
      <div
        className={cn(
          "transition-opacity duration-150",
          collapsed ? "opacity-0" : "opacity-100",
        )}
      >
        <p className="text-lg leading-none font-semibold">Stocks</p>
        <p className="mt-1 text-[0.68rem] text-sidebar-foreground/50">
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
    <div className="min-h-dvh lg:flex">
      <aside
        data-collapsed={collapsed ? "true" : "false"}
        style={
          {
            "--sidebar-expanded": SIDEBAR_WIDTH,
            "--sidebar-rail": SIDEBAR_RAIL_WIDTH,
          } as CSSProperties
        }
        className={cn(
          "sidebar-rail hidden min-h-dvh shrink-0 flex-col overflow-hidden bg-sidebar text-sidebar-foreground lg:sticky lg:top-0 lg:flex lg:h-dvh",
          collapsed
            ? "w-[var(--sidebar-rail)] px-2"
            : "w-[var(--sidebar-expanded)] px-3",
        )}
      >
        <div className="flex w-full flex-1 flex-col">
          <div className="group/brand relative flex h-9 items-center">
            <Brand collapsed={collapsed} />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={collapsed ? "展开侧栏" : "折叠侧栏"}
              title={collapsed ? "展开侧栏" : "折叠侧栏"}
              className={cn(
                "text-sidebar-foreground/60 transition-opacity duration-150 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                collapsed
                  ? "pointer-events-none absolute inset-y-0 left-0 z-10 w-8 rounded-xl bg-sidebar-accent/90 opacity-0 backdrop-blur-sm group-hover/brand:pointer-events-auto group-hover/brand:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100"
                  : "ml-auto opacity-100",
              )}
              onClick={() => setCollapsed((value) => !value)}
            >
              {collapsed ? (
                <PanelLeftOpen aria-hidden="true" className="size-4" />
              ) : (
                <PanelLeftClose aria-hidden="true" className="size-4" />
              )}
            </Button>
          </div>
          <div
            className={cn(
              "flex-1 transition-[margin] duration-[var(--motion-ui)] ease-[var(--ease-out)]",
              collapsed ? "mt-3" : "mt-8",
            )}
          >
            <p
              className={cn(
                "mb-2 px-3 text-[0.66rem] font-medium whitespace-nowrap text-sidebar-foreground/40 transition-opacity duration-150",
                collapsed ? "opacity-0" : "opacity-100",
              )}
            >
              Workspace
            </p>
            <NavigationLinks collapsed={collapsed} />
          </div>
          <div className="border-t border-sidebar-border pt-3">
            <Button
              type="button"
              variant="ghost"
              title="退出登录"
              aria-label={collapsed ? "退出登录" : undefined}
              className="w-full text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={signOut}
            >
              <IconSlot>
                <LogOut aria-hidden="true" className="size-4" />
              </IconSlot>
              <span
                className={cn(
                  "truncate transition-opacity duration-150",
                  collapsed ? "opacity-0" : "opacity-100",
                )}
              >
                退出登录
              </span>
            </Button>
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
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
        <main className="w-full px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
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
