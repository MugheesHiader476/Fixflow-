"use client";

import Link from "next/link";
import { History, ArrowUpRight } from "lucide-react";
import { PageHeading } from "@/components/layout/page-heading";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { listSessions } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import { Button } from "@/components/ui/button";
import { timeAgo } from "@/lib/utils";
import { chatHistory } from "@/lib/chat";

export default function HistoryPage() {
  const { data, loading, error, reload } = useResource(listSessions);
  const sessions = data ?? [];
  const chats = useResource(chatHistory);
  const parents = new Set(chats.data?.map((message) => message.parent_run_id).filter(Boolean));
  const conversations = chats.data?.filter((message) => !parents.has(message.id)) ?? [];

  return (
    <AppShell sessionTitle="Conversation History">
      <div className="ff-page">
        <PageHeading eyebrow="YOUR CONVERSATIONS" title="Pick up the thread." action={<Button size="sm" onClick={() => { reload(); chats.reload(); }} loading={loading || chats.loading}>Refresh history</Button>}>
          Reopen your questions, answers, and follow-ups with their saved evidence.
        </PageHeading>
        {chats.error && <p role="alert" className="text-sm text-danger">{chats.error}</p>}
        {chats.loading && <p role="status" className="text-sm text-muted">Loading chats…</p>}
        <section aria-label="Recent chats" className="space-y-2">
          {conversations.map((message) => <Link key={message.id} href={`/?chat=${encodeURIComponent(message.id)}`} className="flex min-w-0 items-center gap-3 rounded-lg border border-border bg-panel p-4 hover:border-accent/50">
            <History size={16} className="shrink-0 text-accent" /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{message.question}</span><span className="mt-1 block truncate text-xs text-muted">{message.answer?.answer ?? message.error ?? "Preparing an answer…"}</span></span><Badge tone={message.status === "completed" ? "success" : "warning"}>{message.status === "completed" ? "Answered" : message.status}</Badge><ArrowUpRight size={15} className="shrink-0 text-muted" />
          </Link>)}
          {!chats.loading && !chats.error && !conversations.length && <p className="text-sm text-muted">Your new chats will appear here.</p>}
        </section>
        {error && <p className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger">{error}</p>}
        {loading && <p role="status" className="text-sm text-muted">Loading conversation history…</p>}
        <section aria-label="Historical conversations" className="space-y-2">
          {sessions.map((session) => (
            <Link key={session.id} href={`/?session=${encodeURIComponent(session.id)}`} className="flex items-center gap-3 rounded-lg border border-border bg-panel p-4 transition-colors hover:border-accent/50">
              <History size={16} className="shrink-0 text-accent" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{session.title}</span>
                <span className="mt-1 block truncate text-xs text-muted">{session.errorMessage}</span>
              </span>
              <Badge tone={session.status === "resolved" ? "success" : "warning"}>{session.status}</Badge>
              <span className="hidden text-xs text-muted sm:block">{timeAgo(session.createdAt)}</span>
              <ArrowUpRight size={15} className="text-muted" />
            </Link>
          ))}
          {!loading && !sessions.length && !error && <p className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted">No conversations yet.</p>}
        </section>
      </div>
    </AppShell>
  );
}
