"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUp, MessageSquare, Plus, Plug, Square, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { chatMessage, chatThread, sendChatMessage, stopChatMessage, type ChatMessage } from "@/lib/chat";
import { listConnectors } from "@/lib/connectors";
import { useResource } from "@/lib/use-resource";
import { ChatAnswer } from "./chat-answer";

const NAMES = { gmail: "Gmail", google_drive: "Drive", github: "GitHub", slack: "Slack" };

function ChatSources() {
  const apps = useResource(listConnectors);
  return <div className="ff-chat-apps" aria-label="Chat sources">
    <span>Searches your uploads</span>
    {apps.data?.map((app) => {
      const ready = app.accounts.some((account) => ["connected", "syncing", "connected_with_warning"].includes(account.status)
        && account.authentication_status === "valid" && account.configuration.resource_ids.length > 0);
      return <Link key={app.provider} href="/connectors" className={ready ? "ff-chat-app-ready" : ""} title={ready ? "Selected resources available to chat" : "Connect and select resources"}><span aria-hidden="true">{ready ? "●" : "+"}</span>{NAMES[app.provider]}</Link>;
    })}
    {apps.loading && <span>Checking apps…</span>}
    {apps.error && <Link href="/connectors">Check app connections</Link>}
  </div>;
}

export function AgentPanel({ initialMessageId = null, onMessageChange }: {
  initialMessageId?: string | null; onMessageChange?: (id: string | null) => void;
}) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(Boolean(initialMessageId));
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const failures = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const loaded = useRef<string | null>(null);
  const pending = useRef(false);
  const latest = messages.at(-1);
  const activeId = latest && ["queued", "running"].includes(latest.status) ? latest.id : null;
  const disabled = busy || restoring || Boolean(activeId);
  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (!initialMessageId || loaded.current === initialMessageId) return;
    const request = new AbortController();
    void chatThread(initialMessageId, request.signal).then((thread) => {
      if (!request.signal.aborted) {
        loaded.current = initialMessageId;
        setMessages(thread); setPaused(false); setError(null); failures.current = 0;
      }
    }).catch((failure: unknown) => {
      if (!request.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not reopen this chat.");
    }).finally(() => { if (!request.signal.aborted) setRestoring(false); });
    return () => request.abort();
  }, [initialMessageId]);

  function update(message: ChatMessage) {
    setMessages((current) => current.map((item) => item.id === message.id ? message : item));
    if (!["queued", "running"].includes(message.status)) window.dispatchEvent(new Event("fixflow:chats-changed"));
  }

  useEffect(() => {
    if (!activeId || paused) return;
    const request = new AbortController();
    let polling = false;
    const timer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const message = await chatMessage(activeId, request.signal);
        if (!request.signal.aborted) { update(message); failures.current = 0; }
      } catch (failure) {
        if (!request.signal.aborted && ++failures.current >= 3) {
          setPaused(true); setError(failure instanceof Error ? failure.message : "Connection interrupted. Refresh the answer to continue.");
        }
      } finally { polling = false; }
    }, 2000);
    return () => { clearInterval(timer); request.abort(); };
  }, [activeId, paused]);

  async function perform(action: (signal: AbortSignal) => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError(null);
    try { await action(request.signal); }
    catch (failure) { if (!request.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not send your message."); }
    finally { pending.current = false; if (!request.signal.aborted) setBusy(false); }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || !question.trim()) return;
    void perform(async (signal) => {
      const parent = messages.findLast((message) => message.status === "completed");
      const message = await sendChatMessage(question.trim(), parent?.id ?? null, signal);
      if (!signal.aborted) {
        loaded.current = message.id;
        setMessages((current) => [...current, message]); setQuestion("");
        failures.current = 0; setPaused(false); onMessageChange?.(message.id);
        window.dispatchEvent(new Event("fixflow:chats-changed"));
      }
    });
  }

  return <section aria-label="Workspace assistant" className="ff-chat">
    <div className="ff-chat-heading"><h2><MessageSquare size={17} />Ask your workspace</h2>
      {!!messages.length && <Button size="sm" disabled={disabled} onClick={() => {
        setMessages([]); setQuestion(""); setError(null); loaded.current = null; onMessageChange?.(null);
      }}><Plus size={13} />New chat</Button>}
    </div>
    <ChatSources />
    {restoring && <p role="status" className="my-4 text-sm text-muted">Opening conversation…</p>}
    <div className="ff-chat-messages" aria-label="Conversation">
      {messages.length === 20 && messages[0].parent_run_id && <p className="text-xs text-muted">Showing the most recent 20 messages.</p>}
      {messages.map((message) => <article key={message.id} className="ff-chat-turn" aria-label="Chat message">
        <div className="ff-chat-question"><span className="sr-only">You: </span>{message.question}</div>
        <ChatAnswer message={message} />
      </article>)}
    </div>
    {error && <p role="alert" className="my-3 break-words text-sm text-danger">{error}</p>}
    {paused && latest && <Button size="sm" disabled={busy} onClick={() => void perform(async (signal) => {
      const message = await chatMessage(latest.id, signal);
      if (!signal.aborted) { update(message); failures.current = 0; setPaused(false); }
    })}>Refresh answer</Button>}
    <form onSubmit={submit} className="ff-composer" aria-label="Chat">
      <textarea aria-label="Message" required maxLength={4000} rows={3} value={question} disabled={disabled}
        onChange={(event) => setQuestion(event.target.value)} placeholder={messages.length ? "Ask a follow-up…" : "What would you like to know about your work?"}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault(); event.currentTarget.form?.requestSubmit();
          }
        }} />
      <div className="ff-composer-toolbar">
        <div className="ff-composer-options"><Link href="/sources" className="ff-chat-add"><Upload size={15} />Add sources</Link><Link href="/connectors" className="ff-chat-add"><Plug size={15} />Connect apps</Link></div>
        {activeId ? <button type="button" className="ff-composer-submit" aria-label="Stop answer" disabled={busy} onClick={() => void perform(async (signal) => {
          const message = await stopChatMessage(activeId, signal); if (!signal.aborted) { update(message); setPaused(false); }
        })}><Square size={14} /></button> : <button type="submit" className="ff-composer-submit" aria-label="Send message" disabled={disabled || !question.trim()}><ArrowUp size={20} /></button>}
      </div>
    </form>
    <p className="ff-composer-help">Relevant selected app content is searched automatically. Questions and consulted content are sent to xAI. Check cited sources for important decisions.</p>
    <Link href="/history" className="ff-chat-history-link">Your conversations →</Link>
  </section>;
}
