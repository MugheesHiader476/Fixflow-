"use client";

import { useEffect, useRef, useState } from "react";
import { BookOpen, Bot, CornerDownLeft, GitBranch, MessagesSquare, Code2, User } from "lucide-react";
import { FOLLOWUP_SUGGESTIONS } from "@/lib/chat-suggestions";
import { listMessages } from "@/lib/api";
import type { ChatMessage, SourceType } from "@/lib/types";
import { cn } from "@/lib/utils";
import { GroundedAnswerView } from "./grounded-answer";

const SOURCE_ICON: Record<SourceType, React.ReactNode> = {
  docs: <BookOpen size={11} />,
  github: <GitBranch size={11} />,
  community: <MessagesSquare size={11} />,
  code: <Code2 size={11} />,
};

export function FollowUpChat(props: { sessionId: string; confidence: number | null }) {
  return <SessionChat key={props.sessionId} {...props} />;
}

function SessionChat({
  sessionId,
}: {
  sessionId: string;
  confidence: number | null;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [retry, setRetry] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listMessages(sessionId, controller.signal).then((items) => {
      if (!controller.signal.aborted) setMessages(items);
    }).catch(() => {
      if (!controller.signal.aborted) setLoadError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [sessionId, retry]);


  return (
    <section aria-label="Follow-up chat" className="rounded-xl border border-border bg-panel">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Bot size={15} className="text-accent" />
        <h3 className="text-sm font-semibold">Saved conversation</h3>
        <span className="ml-auto hidden text-[11px] text-muted sm:inline">
          saved with this session
        </span>
      </div>

      <div ref={listRef} className="max-h-96 space-y-3 overflow-y-auto px-4 py-4">
        {loading && <p role="status" className="text-sm text-muted">Loading conversation…</p>}
        {loadError && <p role="alert" className="text-sm text-danger">
          Could not load the conversation. <button className="underline" onClick={() => {
            setLoading(true); setLoadError(false); setRetry((value) => value + 1);
          }}>Retry</button>
        </p>}
        {!loading && !loadError && !messages.length && <p className="text-sm text-muted">
          No saved messages in this conversation.
        </p>}
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="ff-fade-up flex justify-end gap-2.5">
              <div className="max-w-[85%] rounded-lg rounded-br-sm border border-accent/25 bg-accent/12 px-3.5 py-2.5">
                <p className="text-[13px] leading-relaxed text-foreground">{m.text}</p>
              </div>
              <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-panel-2 text-muted">
                <User size={13} />
              </span>
            </div>
          ) : (
            <div key={m.id} className="ff-fade-up flex gap-2.5">
              <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent">
                <Bot size={13} />
              </span>
              <div className="max-w-[85%] space-y-2">
                <div className="rounded-lg rounded-tl-sm border border-border bg-background px-3.5 py-2.5">
                  {m.pending && (
                    <span className="flex gap-1 py-1" aria-label="ProofOS is thinking">
                      {[0, 1, 2].map((i) => (
                        <span
                          key={i}
                          className="ff-pulse-dot h-1.5 w-1.5 rounded-full bg-accent"
                          style={{ animationDelay: `${i * 0.2}s` }}
                        />
                      ))}
                    </span>
                  )}
                  {!m.pending && (m.answer ? <GroundedAnswerView answer={m.answer} /> : (
                    <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-foreground/90">{m.text}</p>
                  ))}
                </div>
                {!m.answer && m.sources && m.sources.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {m.sources.map((s) => (
                      <span
                        key={s.id ?? `${s.type}:${s.title}`}
                        className={cn(
                          "inline-flex items-center gap-1 rounded border border-border bg-panel-2 px-2 py-0.5 text-[10px]",
                          s.type === "docs" ? "text-lime/90" : "text-muted"
                        )}
                      >
                        {SOURCE_ICON[s.type]}
                        {s.title}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        )}
      </div>

      <fieldset disabled className="border-t border-border px-4 py-3">
        <p className="mb-2 text-xs text-muted">Follow-up answers were removed. Your saved messages remain available.</p>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {FOLLOWUP_SUGGESTIONS.map((s) => (
            <button
              key={s}
              className="rounded-full border border-border px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            placeholder="Ask a follow-up about your sources…"
            aria-label="Follow-up question"
            maxLength={4000}
            className="h-10 flex-1 rounded-lg border border-border bg-background px-3.5 text-sm text-foreground placeholder:text-muted/50 focus:border-accent/60 focus:outline-none disabled:opacity-60"
          />
          <button
            aria-label="Send follow-up"
            className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent text-accent-foreground transition-colors hover:bg-accent-strong disabled:opacity-40"
          >
            <CornerDownLeft size={16} />
          </button>
        </div>
      </fieldset>
    </section>
  );
}
