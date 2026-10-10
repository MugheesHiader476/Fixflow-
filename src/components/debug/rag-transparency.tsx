"use client";

import { useState } from "react";
import { ChevronDown, Database, Filter, Sparkles } from "lucide-react";
import type { Diagnosis } from "@/lib/types";
import { cn } from "@/lib/utils";

export function RagTransparency({ rag, generation }: { rag: Diagnosis["rag"]; generation?: Diagnosis["generation"] }) {
  const [open, setOpen] = useState(false);

  return (
    <section
      aria-label="RAG transparency"
      className={cn(
        "rounded-xl border bg-panel transition-colors",
        open ? "border-accent/30" : "border-border"
      )}
    >
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-4 py-3 text-left"
      >
        <Sparkles size={15} className="text-lime" />
        <span className="text-sm font-medium">How FixFlow found this answer</span>
        <span className="hidden gap-4 font-mono text-[11px] text-muted sm:flex">
          <span>Retrieved: {rag.retrieved}</span>
          {rag.reranked > 0 && <span>Reranked: {rag.reranked}</span>}
          <span>Sources used: {rag.sourcesUsed}</span>
        </span>
        <ChevronDown size={15} className={cn("ml-auto text-muted transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="ff-fade-up border-t border-border px-4 py-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-1">
              <FlowStep label="Query" mono value={rag.query} />
              <Arrow />
              {rag.expansions.length > 0 && <FlowStep label="Query Expansion">
                <ul className="space-y-1 font-mono text-[11px] text-muted">
                  {rag.expansions.map((e) => (
                    <li key={e} className="flex gap-1.5">
                      <span className="text-lime">+</span> {e}
                    </li>
                  ))}
                </ul>
              </FlowStep>}
              <Arrow />
              <FlowStep label={rag.retrievalMethod === "dense" ? "Semantic retrieval" : "Keyword retrieval"} mono value={`${rag.retrievalMethod === "dense" ? "Private semantic search" : "PostgreSQL full-text search"} · ${rag.retrieved} excerpts`} />
              <Arrow />
              {rag.topChunks.length > 0 && <FlowStep label="Top Documents">
                <ul className="space-y-1">
                  {rag.topChunks.map((c) => (
                    <li key={c.doc} className="flex items-center gap-2 text-[11px]">
                      <Database size={10} className="shrink-0 text-muted" />
                      <span className="truncate font-mono text-foreground/80">{c.doc}</span>
                      <span className="ml-auto font-mono text-lime">{c.score.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </FlowStep>}
              {rag.reranked > 0 && <><Arrow /><FlowStep label="Reranking" value={`${rag.reranked} excerpts reranked`} /></>}
              <Arrow />
              <FlowStep label="Answer preparation" value={generation === "model" ? "Local model with checked source quotes" : "Source excerpts only"} />
            </div>

            <div className="grid grid-cols-2 gap-2 self-start lg:grid-cols-1">
              <Stat label="Excerpts retrieved" value={rag.retrieved} />
              {rag.reranked > 0 && <Stat label="Kept after rerank" value={rag.reranked} />}
              <Stat label="Sources cited" value={rag.sourcesUsed} />
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function FlowStep({
  label,
  value,
  mono,
  children,
}: {
  label: string;
  value?: string;
  mono?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-panel-2/60 px-3 py-2">
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-accent/90">
        <Filter size={10} /> {label}
      </p>
      {value && (
        <p className={cn("mt-1 break-words text-foreground/85", mono ? "font-mono text-xs" : "text-[13px]")}>
          {value}
        </p>
      )}
      {children}
    </div>
  );
}

function Arrow() {
  return (
    <div aria-hidden className="ml-4 h-2.5 border-l border-dashed border-border-strong" />
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2.5 text-center lg:text-left">
      <p className="font-mono text-xl font-semibold text-lime">{value}</p>
      <p className="text-[10px] uppercase tracking-wider text-muted/80">{label}</p>
    </div>
  );
}
