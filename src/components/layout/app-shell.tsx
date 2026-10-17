"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "./sidebar";
import { TopBar } from "./topbar";

const PAGE_META: Record<string, string> = {
  "/": "Workspace",
  "/history": "Conversation history",
  "/saved": "Saved findings",
  "/sources": "Knowledge sources",
  "/connectors": "Connected apps",
  "/settings": "Settings",
};

export function AppShell({
  children, sessionTitle, techs = [], rightPanel, rightPanelOpen = false, onToggleRightPanel,
}: {
  children: ReactNode;
  sessionTitle?: string;
  techs?: string[];
  rightPanel?: ReactNode;
  rightPanelOpen?: boolean;
  onToggleRightPanel?: () => void;
}) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setMobileOpen(false));
    return () => window.cancelAnimationFrame(frame);
  }, [pathname]);

  return (
    <div className={collapsed ? "ff-app ff-sidebar-collapsed" : "ff-app"}>
      <a href="#main-content" className="ff-skip-link">Skip to content</a>
      <Sidebar open={mobileOpen} collapsed={collapsed} onClose={() => setMobileOpen(false)} onCollapse={() => setCollapsed(true)} />
      <div className="ff-app-content">
        <TopBar
          sessionTitle={sessionTitle ?? PAGE_META[pathname] ?? "Workspace"}
          techs={techs}
          onMobileNav={() => setMobileOpen(true)}
          onDesktopNav={() => setCollapsed(!collapsed)}
          sidebarCollapsed={collapsed}
          rightPanelOpen={rightPanelOpen}
          onToggleRightPanel={() => onToggleRightPanel?.()}
          showRightToggle={Boolean(onToggleRightPanel)}
        />
        <div className="ff-main-body">
          <main id="main-content" tabIndex={-1} className="min-w-0 flex-1">{children}</main>
          {rightPanel}
        </div>
      </div>
    </div>
  );
}
