"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { cancelWorkflow, executeWorkflowAction, startWorkflow, workflowHistory, workflowStatus, type WorkflowAction, type WorkflowRun } from "@/lib/agent";

const inputClass = "w-full min-w-0 rounded-lg border border-border bg-panel px-3 py-2 text-sm text-foreground";

function replaceAction(current: WorkflowRun | null, result: WorkflowAction): WorkflowRun | null {
  return current ? { ...current, actions: current.actions.map((item) => item.id === result.id ? result : item) } : current;
}

export function AgentPanel() {
  const [question, setQuestion] = useState("");
  const [project, setProject] = useState("");
  const [connected, setConnected] = useState(true);
  const [automatic, setAutomatic] = useState(false);
  const [emailActions, setEmailActions] = useState(false);
  const [recipients, setRecipients] = useState("");
  const [followup, setFollowup] = useState(false);
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [history, setHistory] = useState<WorkflowRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const failures = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const active = run?.status === "queued" || run?.status === "running";
  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (!run || !active || paused) return;
    const request = new AbortController();
    let polling = false;
    const timer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const current = await workflowStatus(run.id, request.signal);
        if (!request.signal.aborted) { setRun(current); failures.current = 0; }
      } catch (failure) {
        if (!request.signal.aborted && ++failures.current >= 3) {
          setPaused(true); setError(failure instanceof Error ? failure.message : "Status refresh failed. Use Refresh workflow.");
        }
      } finally { polling = false; }
    }, 2000);
    return () => { clearInterval(timer); request.abort(); };
  }, [run, active, paused]);

  async function perform(action: (signal: AbortSignal) => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError(null);
    try { await action(request.signal); }
    catch (failure) { if (!request.signal.aborted) setError(failure instanceof Error ? failure.message : "Workflow request failed."); }
    finally { pending.current = false; if (!request.signal.aborted) setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    void perform(async (signal) => {
      const result = await startWorkflow({ question: question.trim(), project: project.trim() || null,
        include_connections: connected, action_mode: automatic ? "auto" : "draft",
        allow_email_actions: emailActions || automatic,
        allowed_recipients: recipients.split(",").map((value) => value.trim()).filter(Boolean),
        parent_run_id: followup && run?.status === "completed" ? run.id : null }, signal);
      if (!signal.aborted) { setRun(result); failures.current = 0; setPaused(false); }
    });
  }
  async function execute(action: WorkflowAction) {
    await perform(async (signal) => {
      const result = await executeWorkflowAction(action);
      if (!signal.aborted) setRun((current) => replaceAction(current, result));
    });
  }

  return <section aria-label="Workspace assistant" className="my-6 w-full min-w-0 rounded-2xl border border-border bg-panel p-4 text-left sm:p-6">
    <h2 className="text-lg font-medium">Work with your workspace</h2>
    <p className="my-2 text-sm text-muted">Grok can consult your parsed sources and selected connected apps, investigate project status, prepare files, and write email replies. Questions and consulted content are sent to xAI.</p>
    <form onSubmit={submit} className="grid min-w-0 gap-3">
      <label className="text-sm">Task<textarea className={inputClass} required maxLength={4000} rows={3} value={question} disabled={busy || active} onChange={(event) => setQuestion(event.target.value)} placeholder="Review our SRS, the manager’s email, and GitHub progress. Explain the release blockers." /></label>
      <label className="text-sm">Project for indexed search (optional)<input className={inputClass} maxLength={100} value={project} disabled={busy || active} onChange={(event) => setProject(event.target.value)} /></label>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={connected} disabled={busy || active} onChange={(event) => setConnected(event.target.checked)} />Use my currently authorized connected apps and their selected resources</label>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={emailActions} disabled={busy || active} onChange={(event) => setEmailActions(event.target.checked)} />Allow email drafts and replies for this request</label>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={automatic} disabled={busy || active} onChange={(event) => setAutomatic(event.target.checked)} />Authorize automatic email sending to the addresses below for this request</label>
      <label className="text-sm">Authorized automatic recipients (comma separated)<input className={inputClass} type="text" maxLength={2540} value={recipients} disabled={busy || active} required={automatic} onChange={(event) => setRecipients(event.target.value)} placeholder="manager@example.com" /></label>
      <p className="text-xs text-muted">Other email messages stay as drafts for review. Sending requires Gmail send permission. Connected-app selections determine live scope; the project label narrows indexed search.</p>
      {run?.status === "completed" && <label className="flex gap-2 text-sm"><input type="checkbox" checked={followup} onChange={(event) => setFollowup(event.target.checked)} />Continue this conversation</label>}
      <Button type="submit" variant="primary" loading={busy} disabled={!question.trim() || active}>Run workflow</Button>
    </form>
    <div className="mt-3 flex flex-wrap gap-2">
      <Button disabled={busy} onClick={() => void perform(async (signal) => { const records = await workflowHistory(signal); if (!signal.aborted) setHistory(records); })}>Load workflow history</Button>
      {run && <Button disabled={busy} onClick={() => void perform(async (signal) => { const result = await workflowStatus(run.id, signal); if (!signal.aborted) { setRun(result); failures.current = 0; setPaused(false); } })}>Refresh workflow</Button>}
      {active && run && <Button disabled={busy} onClick={() => void perform(async () => { setRun(await cancelWorkflow(run.id)); })}>Stop further steps</Button>}
    </div>
    {!!history.length && <label className="mt-3 block text-sm">Previous workflow<select className={inputClass} value={run?.id ?? ""} onChange={(event) => { const previous = history.find((item) => item.id === event.target.value); if (previous) { setRun(previous); setQuestion(previous.question); setPaused(false); } }}><option value="">Select a workflow</option>{history.map((item) => <option key={item.id} value={item.id}>{item.question.slice(0, 80)} · {item.status}</option>)}</select></label>}
    {error && <p role="alert" className="mt-3 break-words text-sm text-danger">{error}</p>}
    {run && <div aria-live="polite" className="mt-4 min-w-0 space-y-3">
      <p role="status" className="text-xs text-muted">{run.status} · {run.steps} model step(s) · {run.usage.input_tokens ?? "unknown"} input tokens · {run.usage.output_tokens ?? "unknown"} output tokens</p>
      {run.error && <p className="break-words text-sm">{run.error}</p>}
      {run.answer && <><p className="whitespace-pre-wrap break-words text-sm">{run.answer.answer}</p>{run.answer.claims.map((claim, index) => <p key={index} className="whitespace-pre-wrap break-words text-sm">{claim.text} {claim.citations.map((citation) => <a className="underline" key={citation.evidence_id} href={`#${citation.evidence_id}`}>[{run.materials.findIndex((item) => item.id === citation.evidence_id) + 1}]</a>)}</p>)}{run.answer.limitations.map((item, index) => <p key={index} className="break-words text-xs text-muted">{item}</p>)}</>}
      {run.actions.map((action) => <article key={action.id} className="min-w-0 rounded-xl border border-border p-3 text-sm">
        <p className="break-words">Email: {action.status} · {action.payload.recipient}</p><p className="break-words">{action.payload.subject}</p><pre className="mt-2 whitespace-pre-wrap break-words text-xs">{action.payload.body}</pre>
        {action.error && <p className="mt-2 break-words text-xs text-danger">{action.error}</p>}
        {action.status === "sent" && <p className="text-xs text-muted">Gmail receipt: {action.provider_message_id}</p>}
        {["draft", "blocked"].includes(action.status) && action.payload.body && <Button className="mt-2" disabled={busy || active} onClick={() => void execute(action)}>Send this exact email</Button>}
        {["unknown", "dispatching"].includes(action.status) && <p className="mt-2 text-xs text-muted">Delivery may be uncertain. Check Gmail before preparing another message.</p>}
      </article>)}
      {run.files.map((file) => <a className="block break-all text-sm underline" key={file.id} href={`/api/backend${file.download_path}`} download>{file.filename}</a>)}
      {run.materials.map((source) => <details id={source.id} key={source.id} className="min-w-0 rounded-xl border border-border p-3 text-xs"><summary className="cursor-pointer break-words">{source.title} · observed {source.observed_at}</summary><pre className="mt-2 whitespace-pre-wrap break-words">{source.text}</pre>{source.warning && <p className="mt-2 text-muted">{source.warning}</p>}<p className="mt-2 break-all text-muted">Source: {String(source.reference.source_id ?? source.reference.resource_id ?? source.reference.provider ?? "workspace")}{source.reference.path ? " · " + String(source.reference.path) : ""}</p></details>)}
    </div>}
  </section>;
}
