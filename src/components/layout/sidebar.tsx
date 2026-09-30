"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Bookmark, Database, History, Menu, Plus, Settings, X, ArrowUpRight } from "lucide-react";
import { timeAgo } from "@/lib/utils";
import { listSessions } from "@/lib/api";
import type { DebugSession } from "@/lib/types";
import { Brand } from "./brand";

export function MobileNavButton({ onClick }: { onClick: () => void }) {
  return <button type="button" onClick={onClick} aria-label="Open menu" aria-haspopup="dialog" className="ff-menu-button"><Menu size={18} /><span>Menu</span></button>;
}

const NAV = [
  { href: "/", label: "New Debug Session", icon: Plus },
  { href: "/sources", label: "Knowledge Sources", icon: Database },
  { href: "/history", label: "Debug History", icon: History },
  { href: "/saved", label: "Saved Solutions", icon: Bookmark },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
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
          if (!controller.signal.aborted) { setSessions(items.slice(0, 4)); setLoadError(false); }
        })
        .catch(() => { if (!controller.signal.aborted) { setSessions([]); setLoadError(true); } });
    };
    refresh();
    window.addEventListener("fixflow:sessions-changed", refresh);
    return () => { controller.abort(); window.removeEventListener("fixflow:sessions-changed", refresh); };
  }, []);

  return (
    <dialog ref={dialog} className="ff-menu-dialog" aria-label="Workspace menu" onCancel={onClose} onClose={onClose}>
      <div className="ff-menu-header"><Brand /><button type="button" onClick={onClose} aria-label="Close menu"><X size={22} /></button></div>
      <p className="ff-eyebrow">YOUR WORKSPACE</p>
      <nav aria-label="Workspace navigation" className="ff-drawer-nav">
        {NAV.map((item) => <Link key={item.href} href={item.href} onClick={onClose} aria-current={pathname === item.href ? "page" : undefined}><item.icon size={18} />{item.label}<ArrowUpRight size={16} /></Link>)}
      </nav>
      <div className="ff-recent-sessions"><p className="ff-eyebrow">RECENT SESSIONS</p>
        {sessions.map((session) => <Link key={session.id} href={`/?session=${encodeURIComponent(session.id)}`} onClick={onClose}><span>{session.title}</span><small>{session.technology[0] || "Documentation"} · {timeAgo(session.createdAt)}</small></Link>)}
        {!sessions.length && <p className="text-sm text-muted">{loadError ? "Could not load recent sessions. Open history to retry." : "Your next investigation starts here."}</p>}
      </div>
    </dialog>
  );
}
