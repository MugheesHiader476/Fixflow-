"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Bookmark, Database, History, PanelLeftClose, Plug, Plus, Settings, ShieldCheck, X } from "lucide-react";
import { timeAgo } from "@/lib/utils";
import { listSessions } from "@/lib/api";
import type { DebugSession } from "@/lib/types";
import { Brand } from "./brand";

const NAV = [
  { href: "/", label: "New workspace", icon: Plus },
  { href: "/sources", label: "Knowledge Sources", icon: Database },
  { href: "/connectors", label: "Connected Apps", icon: Plug },
  { href: "/history", label: "Conversation History", icon: History },
  { href: "/saved", label: "Saved findings", icon: Bookmark },
];

export function Sidebar({ open, collapsed, onClose, onCollapse }: {
  open: boolean; collapsed: boolean; onClose: () => void; onCollapse: () => void;
}) {
  const pathname = usePathname();
  const dialog = useRef<HTMLDialogElement>(null);
  const [sessions, setSessions] = useState<DebugSession[]>([]);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      void listSessions(controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) { setSessions(items.slice(0, 16)); setLoadError(false); }
        })
        .catch(() => { if (!controller.signal.aborted) { setSessions([]); setLoadError(true); } });
    };
    refresh();
    window.addEventListener("fixflow:sessions-changed", refresh);
    return () => { controller.abort(); window.removeEventListener("fixflow:sessions-changed", refresh); };
  }, []);

  const content = (mobile: boolean) => <>
    <div className="ff-sidebar-head">
      <Brand />
      <button type="button" onClick={mobile ? onClose : onCollapse} aria-label={mobile ? "Close menu" : "Collapse sidebar"} className="ff-icon-button">
        {mobile ? <X size={18} /> : <PanelLeftClose size={18} />}
      </button>
    </div>
    <nav aria-label={mobile ? "Mobile workspace navigation" : "Workspace navigation"} className="ff-sidebar-nav">
      {NAV.map((item) => <Link key={item.href} href={item.href} onClick={mobile ? onClose : undefined}
        aria-current={pathname === item.href ? "page" : undefined}>
        <item.icon size={18} strokeWidth={1.6} /><span>{item.label}</span>
        {item.href === "/" && <kbd>New</kbd>}
      </Link>)}
    </nav>
    <div className="ff-sidebar-history">
      <div className="ff-sidebar-label"><span>Recent conversations</span><Link href="/history" aria-label="View all conversations"><History size={14} /></Link></div>
      {sessions.map((session) => <Link key={session.id} href={"/?session=" + encodeURIComponent(session.id)} onClick={mobile ? onClose : undefined}
        title={session.title}>
        <span className="ff-session-dot" /><span>{session.title}</span><small>{timeAgo(session.createdAt)}</small>
      </Link>)}
      {!sessions.length && <p>{loadError ? "Could not load conversations. Open history to retry." : "Your past conversations will appear here."}</p>}
    </div>
    <div className="ff-sidebar-bottom">
      <Link href="/settings" onClick={mobile ? onClose : undefined} aria-current={pathname === "/settings" ? "page" : undefined}><Settings size={18} />Settings</Link>
      <div className="ff-sidebar-account"><span className="ff-account-avatar">F</span><div><strong>Private workspace</strong><span><ShieldCheck size={11} />Only your selected sources</span></div></div>
    </div>
  </>;

  return <>
    <aside id="desktop-sidebar" className="ff-sidebar" aria-label="Workspace sidebar" hidden={collapsed}>{content(false)}</aside>
    <dialog ref={dialog} className="ff-menu-dialog" aria-label="Workspace menu" onCancel={onClose} onClose={onClose}>
      {open && content(true)}
    </dialog>
  </>;
}
