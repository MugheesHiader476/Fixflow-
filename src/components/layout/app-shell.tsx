"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { Brand } from "./brand";
import { Sidebar } from "./sidebar";
import { TopBar } from "./topbar";

const PAGE_META: Record<string, { title: string; techs: string[] }> = {
  "/": { title: "New Question", techs: [] },
  "/history": { title: "Conversation History", techs: [] },
  "/saved": { title: "Saved Answers", techs: [] },
  "/sources": { title: "Knowledge Sources", techs: [] },
  "/settings": { title: "Settings", techs: [] },
};

export function AppShell({
  children,
  sessionTitle,
  techs = [],
  rightPanel,
  rightPanelOpen = false,
  onToggleRightPanel,
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
  const meta = PAGE_META[pathname] ?? PAGE_META["/"];
  const showRightToggle = Boolean(onToggleRightPanel);

  useEffect(() => {
    const close = window.requestAnimationFrame(() => setMobileOpen(false));
    return () => window.cancelAnimationFrame(close);
  }, [pathname]);

  const title = sessionTitle ?? meta.title;
  const panelTechs = techs.length ? techs : meta.techs;

  return (
    <div className="ff-app">
      <a href="#main-content" className="ff-skip-link">Skip to content</a>
      <header className="ff-site-header">
        <Brand />
        <nav className="ff-main-nav" aria-label="Main navigation">
          {[
            { href: "/", label: "Workspace" },
            { href: "/sources", label: "Knowledge" },
            { href: "/history", label: "History" },
            { href: "/saved", label: "Saved" },
          ].map((item) => <Link key={item.href} href={item.href} aria-current={pathname === item.href ? "page" : undefined}>{item.label}</Link>)}
        </nav>
        <Link href="/sources" className="ff-pill ff-pill-dark ff-header-cta">Add a source <ArrowUpRight size={17} /></Link>
      </header>
      <Sidebar open={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          sessionTitle={title}
          techs={panelTechs}
          onMobileNav={() => setMobileOpen(true)}
          rightPanelOpen={rightPanelOpen}
          onToggleRightPanel={() => onToggleRightPanel?.()}
          showRightToggle={showRightToggle}
        />
        <div className="ff-main-body">
          <main id="main-content" className="min-w-0 flex-1">{children}</main>
          {rightPanel}
        </div>
      </div>
      <footer className="ff-footer">
        <div><Brand /><p>A little context goes a long way.</p></div>
        <nav aria-label="Footer navigation"><Link href="/sources">Knowledge sources</Link><Link href="/history">Conversation history</Link><Link href="/saved">Saved answers</Link><Link href="/settings">Settings <ArrowUpRight size={14} /></Link></nav>
        <div className="ff-footer-bottom"><span>FixFlow · Your knowledge workspace</span><span>Built around your sources.</span></div>
      </footer>
    </div>
  );
}
