"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUp, Check, ChevronDown, FileText, LoaderCircle, Plus, Plug, ShieldCheck, Upload } from "lucide-react";
import { addKnowledgeSource } from "@/lib/api";

function ComposerOptions() {
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<"add" | "mode" | null>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const modeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        (open === "add" ? addButton : modeButton).current?.focus();
        setOpen(null);
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <div ref={root} className="ff-composer-options">
    <div className="ff-composer-add">
      <button ref={addButton} type="button" className="ff-icon-button" aria-label="Add a source" aria-expanded={open === "add"} aria-controls="source-options" onClick={() => setOpen(open === "add" ? null : "add")}><Plus size={21} /></button>
      {open === "add" && <div id="source-options" className="ff-composer-popover" aria-label="Source options">
        <p>Add to your workspace</p>
        <Link href="/sources"><Upload size={17} /><span>Upload a document<small>Files, spreadsheets, code and more</small></span></Link>
        <Link href="/connectors"><Plug size={17} /><span>Connect an app<small>Gmail, Drive, GitHub and Slack</small></span></Link>
      </div>}
    </div>
    <span className="ff-composer-mode"><FileText size={13} />Sources</span>
    <button type="button" className="ff-chat-disabled" disabled title="AI conversations are not enabled">Chat <span>Soon</span></button>
    <div className="ff-composer-config">
      <button ref={modeButton} type="button" aria-expanded={open === "mode"} aria-controls="parsing-options" onClick={() => setOpen(open === "mode" ? null : "mode")}>Parsing only <ChevronDown size={13} /></button>
      {open === "mode" && <div id="parsing-options" className="ff-composer-popover" aria-label="Workspace capabilities">
        <p>Workspace capabilities</p>
        <div><FileText size={17} /><span>Document parsing<small>Preserve content and source structure</small></span><Check size={16} /></div>
        <div className="ff-unavailable"><span>AI models<small>Not enabled in this workspace</small></span></div>
        <Link href="/settings">Workspace settings <ChevronDown size={13} /></Link>
      </div>}
    </div>
  </div>;
}

export function SourceComposer() {
  const [content, setContent] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!content.trim() || inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError(null); setAccepted(false);
    const controller = new AbortController();
    request.current = controller;
    try {
      await addKnowledgeSource({ kind: "docs", value: title.trim() || "Workspace note", content, ingestionFormat: "document" }, controller.signal);
      if (controller.signal.aborted) return;
      setContent(""); setTitle(""); setAccepted(true);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not add this source. Please try again.");
    } finally {
      inFlight.current = false;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <div className="ff-composer-wrap">
    <form className="ff-composer" onSubmit={(event) => void submit(event)} aria-label="Add source content">
      {content.length > 0 && <input className="ff-note-title" aria-label="Source title" placeholder="Give this source a title (optional)" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} disabled={busy} />}
      <textarea aria-label="Source content" placeholder="Paste a document, a note, a little context…" value={content} maxLength={100000} disabled={busy}
        onChange={(event) => { setContent(event.target.value); setAccepted(false); }}
        aria-describedby="composer-help" rows={3} />
      <div className="ff-composer-toolbar"><ComposerOptions /><button type="submit" className="ff-composer-submit" aria-label="Add content for parsing" disabled={!content.trim() || busy}>{busy ? <LoaderCircle size={19} className="ff-spin" /> : <ArrowUp size={20} />}</button></div>
    </form>
    <p id="composer-help" className="ff-composer-help"><ShieldCheck size={12} />Private to your account. Content is parsed, not sent to an AI model.</p>
    {accepted && <p role="status" className="ff-composer-message">Source accepted. Parsing is queued. <Link href="/sources">View source status →</Link></p>}
    {error && <p role="alert" className="ff-composer-message text-danger">{error}</p>}
  </div>;
}
