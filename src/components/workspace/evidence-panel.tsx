"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { listKnowledgeSources } from "@/lib/api";
import { askEvidence, embeddingStatus, indexEvidence, prepareEvidence, type EvidenceAnswer } from "@/lib/evidence";
import type { KnowledgeSource } from "@/lib/types";

const inputClass = "w-full min-w-0 rounded-lg border border-border bg-panel px-3 py-2 text-sm text-foreground";

export function EvidencePanel() {
  const [project, setProject] = useState("");
  const [question, setQuestion] = useState("");
  const [synthesize, setSynthesize] = useState(false);
  const [retrieval, setRetrieval] = useState<"lexical" | "hybrid">("lexical");
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [source, setSource] = useState("");
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<EvidenceAnswer | null>(null);
  const inFlight = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    const request = new AbortController();
    controller.current = request;
    setBusy(true); setMessage(""); setResult(null);
    try { await action(request.signal); }
    catch (error) { if (!request.signal.aborted) setMessage(error instanceof Error ? error.message : "The request failed."); }
    finally { inFlight.current = false; if (!request.signal.aborted) setBusy(false); }
  }

  function showPreparation(receipt: Awaited<ReturnType<typeof prepareEvidence>>) {
    const errorMessage = receipt.error ? " " + receipt.error : "";
    setMessage(`${receipt.status}: ${receipt.prepared} passages prepared; ${receipt.remaining} remaining. ${receipt.embedding_calls} embedding API call(s). ${receipt.active ? "Semantic search active." : "Lexical search remains available."}${errorMessage}`);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(async (signal) => {
      const answer = await askEvidence(question.trim(), project.trim(), synthesize, signal, retrieval);
      if (!signal.aborted) setResult(answer);
    });
  }

  return <section aria-label="Project evidence" className="my-6 w-full min-w-0 rounded-2xl border border-border bg-panel p-4 text-left sm:p-6">
    <h2 className="text-lg font-medium">Ask your project sources</h2>
    <p className="my-2 text-sm text-muted">Search parsed snapshots added to a project. Exact counts and live status require <Link className="underline" href="/connectors">native connector tools</Link>.</p>
    <form onSubmit={submit} className="grid min-w-0 gap-3">
      <label className="text-sm">Project<input required maxLength={100} className={inputClass} value={project} disabled={busy} placeholder="Atlas" onChange={(event) => { setProject(event.target.value); setResult(null); }} /></label>
      <label className="text-sm">Question<textarea required maxLength={2000} rows={2} className={inputClass} value={question} disabled={busy} placeholder="Why is the release delayed?" onChange={(event) => { setQuestion(event.target.value); setResult(null); }} /></label>
      <label className="text-sm">Search mode<select className={inputClass} value={retrieval} disabled={busy} onChange={(event) => { setRetrieval(event.target.value === "hybrid" ? "hybrid" : "lexical"); setResult(null); }}><option value="lexical">Lexical (local)</option><option value="hybrid">Hybrid (sends question to Gemini)</option></select></label>
      {retrieval === "hybrid" && <p className="text-xs text-muted">Prepare source embeddings below first. Hybrid uses at most one query embedding call; unavailable or partial semantic coverage is reported.</p>}
      <label className="flex items-start gap-2 text-sm text-muted"><input type="checkbox" checked={synthesize} disabled={busy} onChange={(event) => setSynthesize(event.target.checked)} />Generate a cited answer (sends selected evidence to the configured model)</label>
      <Button type="submit" variant="primary" loading={busy} disabled={!project.trim() || !question.trim()}>Search evidence</Button>
    </form>
    <details className="mt-4 text-sm">
      <summary className="cursor-pointer">Add a parsed source to this project</summary>
      <div className="mt-3 grid min-w-0 gap-3">
        <p className="text-xs text-muted">Each source belongs to one search project. Adding it again changes that assignment.</p>
        <Button disabled={busy} onClick={() => void run(async (signal) => {
          const records = await listKnowledgeSources(signal);
          if (!signal.aborted) { setSources(records.filter((item) => item.status === "parsed" && item.access_available)); setMessage("Source list refreshed."); }
        })}>Refresh parsed sources</Button>
        <label>Source<select className={inputClass} value={source} disabled={busy} onChange={(event) => setSource(event.target.value)}><option value="">Select a source</option>{sources.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Display title (optional)<input maxLength={200} className={inputClass} value={title} disabled={busy} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>Document date (optional, supplied by you)<input type="date" className={inputClass} value={date} disabled={busy} onChange={(event) => setDate(event.target.value)} /></label>
        <Button disabled={busy || !project.trim() || !source} onClick={() => void run(async (signal) => {
          const receipt = await indexEvidence(source, project.trim(), title.trim(), date, signal);
          if (!signal.aborted) setMessage(`${receipt.passages} passages added to ${receipt.project}.`);
        })}>Add source to project</Button>
        <p className="text-xs text-muted">Semantic preparation queues this source for background processing and sends passages to Google in batches of up to 16. Completed batches and unchanged inputs are reused. Semantic search activates when the whole source is ready.</p>
        <Button disabled={busy || !source} onClick={() => void run(async (signal) => {
          const receipt = await prepareEvidence(source, signal);
          if (!signal.aborted) showPreparation(receipt);
        })}>Prepare semantic search</Button>
        <Button disabled={busy || !source} onClick={() => void run(async (signal) => {
          const receipt = await embeddingStatus(source, signal);
          if (!signal.aborted) showPreparation(receipt);
        })}>Refresh semantic status</Button>
      </div>
    </details>
    {message && <p role="status" className="mt-3 break-words text-sm">{message}</p>}
    {result && <div aria-live="polite" className="mt-5 min-w-0 space-y-3">
      <p className="text-xs text-muted">{result.evidence.retrieval} search · {result.evidence.embedding_calls} embedding call(s) · {result.evidence.model_calls} synthesis call(s)</p>
      {result.claims.length ? result.claims.map((claim, i) => <p key={i} className="whitespace-pre-wrap break-words text-sm">{claim.text} {claim.citations.map((citation) => <a className="underline" key={citation.passage_id} href={`#${citation.passage_id}`}>[{result.evidence.passages.findIndex(({ passage }) => passage.id === citation.passage_id) + 1}]</a>)}</p>) : <p className="whitespace-pre-wrap break-words text-sm">{result.text}</p>}
      {result.evidence.warnings.map((warning, i) => <p key={i} className="break-words text-xs text-muted">{warning}</p>)}
      {result.evidence.passages.map(({ passage }) => <details id={passage.id} key={passage.id} className="min-w-0 rounded-xl border border-border p-3">
        <summary className="cursor-pointer break-all text-xs">{passage.id} · {passage.tokens} tokens</summary>
        <pre className="mt-2 whitespace-pre-wrap break-words text-xs">{passage.prefix}{"\n\n"}{passage.content}</pre>
        <p className="mt-2 break-all text-xs text-muted">Source: {passage.source_id} · SHA-256: {passage.source_hash}</p>
        {passage.spans.map((span, i) => <p key={i} className="break-all text-xs text-muted">{span.block_id}: {span.unit} {span.start}–{span.end}{span.provenance.page ? ` · page ${span.provenance.page}` : ""}{span.provenance.line_start ? ` · lines ${span.provenance.line_start}–${span.provenance.line_end}` : ""}{span.provenance.sheet ? ` · ${span.provenance.sheet} ${span.provenance.cell_range ?? ""}` : ""}</p>)}
      </details>)}
    </div>}
  </section>;
}
