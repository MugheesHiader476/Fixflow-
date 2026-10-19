"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { executeWorkflowAction, type WorkflowAction } from "@/lib/agent";
import type { ChatMessage } from "@/lib/chat";

function PreviousEmail({ original }: { original: WorkflowAction }) {
  const [action, setAction] = useState(original);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function execute() {
    setBusy(true); setError(null);
    try { setAction(await executeWorkflowAction(action)); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Could not send this email."); }
    finally { setBusy(false); }
  }
  return <details className="mt-3 min-w-0 rounded-xl border border-border p-3 text-sm">
    <summary>Previously prepared email · {action.status}</summary>
    <p className="break-words">{action.payload.recipient}</p><p className="break-words">{action.payload.subject}</p>
    <pre className="mt-2 whitespace-pre-wrap break-words text-xs">{action.payload.body}</pre>
    {(error || action.error) && <p role="alert" className="mt-2 text-danger">{error || action.error}</p>}
    {["draft", "blocked"].includes(action.status) && action.payload.body && <Button className="mt-2" loading={busy} onClick={() => void execute()}>Send this exact email</Button>}
    {action.status === "sent" && <p className="text-xs text-muted">Gmail receipt: {action.provider_message_id}</p>}
    {["unknown", "dispatching"].includes(action.status) && <p className="text-xs text-muted">Delivery may be uncertain. Check Gmail before preparing another message.</p>}
  </details>;
}

export function ChatAnswer({ message }: { message: ChatMessage }) {
  const active = ["queued", "running"].includes(message.status);
  return <div className="ff-chat-answer">
    <span className="ff-chat-author">FixFlow</span>
    {active && <p role="status" className="ff-chat-thinking"><span className="ff-spin h-3 w-3 rounded-full border-2 border-current border-t-transparent" />Searching your sources and connected apps…</p>}
    {message.error && <p className="my-2 break-words text-sm">{message.error}</p>}
    {message.status === "cancelled" && !message.error && <p className="text-sm text-muted">Answer stopped.</p>}
    {message.answer && <div className="space-y-3">
      <p className="whitespace-pre-wrap break-words text-sm">{message.answer.answer}</p>
      {message.answer.claims.map((claim, index) => <p key={index} className="whitespace-pre-wrap break-words text-sm">{claim.text} {claim.citations.map((citation, citeIndex) => <a className="ff-chat-citation" key={`${citation.evidence_id}-${citeIndex}`} title={citation.quote} href={`#source-${message.id}-${citation.evidence_id}`}>[{message.materials.findIndex((source) => source.id === citation.evidence_id) + 1}]</a>)}</p>)}
      {message.answer.limitations.map((item, index) => <p key={index} className="break-words text-xs text-muted">{item}</p>)}
    </div>}
    {message.files.map((file) => <a className="mt-3 block break-all text-sm underline" key={file.id} href={`/api/backend${file.download_path}`} download>{file.filename}</a>)}
    {!!message.materials.length && <details className="ff-chat-sources"><summary>{message.materials.length} consulted source{message.materials.length === 1 ? "" : "s"}</summary>
      {message.materials.map((source, index) => <details id={`source-${message.id}-${source.id}`} key={source.id} className="mt-2 min-w-0 rounded-lg border border-border p-3 text-xs"><summary className="cursor-pointer break-words">[{index + 1}] {source.title}</summary><p className="my-2 text-muted">Observed {new Date(source.observed_at).toLocaleString()}</p><pre className="whitespace-pre-wrap break-words">{source.text}</pre>{source.warning && <p className="mt-2 text-muted">{source.warning}</p>}</details>)}
    </details>}
    {message.actions.map((action) => <PreviousEmail key={action.id} original={action} />)}
  </div>;
}
