"use client";

import { useMemo, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  ExternalLink,
  GitBranch,
  MessagesSquare,
  Scale,
  Search,
  Target,
  Wrench,
  X,
  BookmarkPlus,
  CircleAlert,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CodeBlock } from "@/components/ui/code-block";
import { useToast } from "@/components/ui/toast";
import type { Diagnosis, SourceType } from "@/lib/types";
import { SOURCE_TYPE_LABEL } from "@/lib/api";
import { diagnosisMarkdown } from "@/lib/diagnosis";
import { cn, safeExternalUrl } from "@/lib/utils";
import { RagTransparency } from "./rag-transparency";
import { FollowUpChat } from "./followup-chat";
import { GroundedAnswerView } from "./grounded-answer";

const TYPE_ICON: Record<SourceType, React.ReactNode> = {
  docs: <BookOpen size={13} />,
  github: <GitBranch size={13} />,
  community: <MessagesSquare size={13} />,
  code: <Code2 size={13} />,
};

const TYPE_TONE: Record<SourceType, string> = {
  docs: "text-lime",
  github: "text-accent",
  community: "text-warning",
  code: "text-success",
};

const SOURCE_FILTERS: { id: SourceType | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "docs", label: "Documentation" },
  { id: "github", label: "GitHub" },
  { id: "community", label: "Community" },
  { id: "code", label: "Code" },
];

const DIAGNOSIS_STATUS: Record<
  Diagnosis["status"],
  { label: string; icon: LucideIcon; border: string; iconStyle: string }
> = {
  "likely-cause-found": {
    label: "Likely Cause Found",
    icon: Check,
    border: "border-success/25",
    iconStyle: "bg-success/12 text-success",
  },
  investigating: {
    label: "Investigation in Progress",
    icon: Search,
    border: "border-warning/25",
    iconStyle: "bg-warning/12 text-warning",
  },
  "no-cause": {
    label: "No Cause Identified",
    icon: CircleAlert,
    border: "border-danger/25",
    iconStyle: "bg-danger/12 text-danger",
  },
};

function relevanceTone(relevance: number): string {
  if (relevance >= 90) return "bg-lime";
  if (relevance >= 80) return "bg-accent";
  return "bg-warning";
}

export function DiagnosisResult({
  diagnosis,
  onSaved,
  saving = false,
  saved = false,
}: {
  diagnosis: Diagnosis;
  onSaved: () => void;
  saving?: boolean;
  saved?: boolean;
}) {
  const [sourceFilter, setSourceFilter] = useState<SourceType | "all">("all");
  const [expanded, setExpanded] = useState<string[]>([diagnosis.sources[0]?.id ?? ""]);
  const { toast } = useToast();
  const status = DIAGNOSIS_STATUS[diagnosis.status];
  const StatusIcon = status.icon;
  let statusLabel = status.label;
  if (diagnosis.answer) statusLabel = diagnosis.answer.status === "answered" ? "Answer with citations" : "More evidence needed";

  const visible = useMemo(
    () =>
      sourceFilter === "all"
        ? diagnosis.sources
        : diagnosis.sources.filter((s) => s.type === sourceFilter),
    [sourceFilter, diagnosis.sources]
  );

  const toggleSource = (id: string) =>
    setExpanded((e) => (e.includes(id) ? e.filter((x) => x !== id) : [...e, id]));

  const exportDiagnosis = async () => {
    try {
      await navigator.clipboard.writeText(diagnosisMarkdown(diagnosis));
      toast("Diagnosis report copied as Markdown", "success");
    } catch {
      toast("Could not copy the diagnosis report", "error");
    }
  };

  return (
    <div className="space-y-4">
      <section
        aria-label="Diagnosis summary"
        className={cn("ff-fade-up rounded-xl border bg-panel p-4 sm:p-5", status.border)}
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2.5">
            <span
              className={cn(
                "flex h-9 w-9 items-center justify-center rounded-full",
                status.iconStyle
              )}
            >
              <StatusIcon size={18} strokeWidth={2.5} />
            </span>
            <div>
              <p className="text-sm font-semibold">{statusLabel}</p>
              <p className="text-[11px] text-muted">{diagnosis.rag.sourcesUsed} sources {diagnosis.answer ? "cited" : "retrieved"}</p>
            </div>
          </div>
          <div className="h-8 w-px bg-border max-sm:hidden" />
          {diagnosis.confidence !== null && <div>
            <p className="text-[11px] uppercase tracking-wider text-muted/70">Confidence</p>
            <div className="flex items-center gap-2">
              <p className={cn("font-mono text-lg font-semibold", diagnosis.confidence >= 85 ? "text-success" : "text-warning")}>
                {diagnosis.confidence}%
              </p>
              <div className="h-1.5 w-20 overflow-hidden rounded-full bg-panel-2">
                <div
                  className={cn("h-full rounded-full", diagnosis.confidence >= 85 ? "bg-success" : "bg-warning")}
                  style={{ width: `${diagnosis.confidence}%` }}
                />
              </div>
            </div>
          </div>}
          <div className="h-8 w-px bg-border max-sm:hidden" />
          {diagnosis.detected.length > 0 && <div>
            <p className="text-[11px] uppercase tracking-wider text-muted/70">Selected technologies</p>
            <div className="mt-0.5 flex gap-1.5">
              {diagnosis.detected.map((t) => (
                <Badge key={t} tone="lime" className="font-mono">
                  {t}
                </Badge>
              ))}
            </div>
          </div>}
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" onClick={onSaved} loading={saving} disabled={saved}>
              <BookmarkPlus size={14} /> {saved ? "Saved" : "Save"}
            </Button>
            <Button variant="secondary" size="sm" onClick={exportDiagnosis}>
              Export
            </Button>
          </div>
        </div>
      </section>
      {diagnosis.generation === "disabled" && <p role="status" className="rounded-lg border border-border bg-panel p-3 text-sm text-muted">
        Documentation search is available. AI diagnosis and suggested code changes are not connected yet.
      </p>}

      {diagnosis.answer && <GroundedAnswerView answer={diagnosis.answer} />}
      {!diagnosis.answer && <div className="grid gap-4 lg:grid-cols-2">
        <ResultCard icon={<Target size={15} className="text-accent" />} title="Root Cause">
          <p className="text-sm leading-relaxed text-foreground/85">{diagnosis.rootCause}</p>
        </ResultCard>
        <ResultCard icon={<CircleAlert size={15} className="text-warning" />} title="Why This Happens">
          <p className="text-sm leading-relaxed text-foreground/85">{diagnosis.whyThisHappens}</p>
        </ResultCard>
      </div>}

      {diagnosis.recommendedFix.length > 0 && <ResultCard icon={<Wrench size={15} className="text-accent" />} title="Recommended Fix">
        <ol className="space-y-2.5">
          {diagnosis.recommendedFix.map((step, i) => (
            <li key={`${step.title}:${step.detail}`} className="flex gap-3">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded bg-accent/15 font-mono text-[11px] font-semibold text-accent">
                {i + 1}
              </span>
              <div>
                <p className="text-sm font-medium">{step.title}</p>
                <p className="mt-0.5 text-[13px] leading-relaxed text-muted">{step.detail}</p>
              </div>
            </li>
          ))}
        </ol>
      </ResultCard>}

      {diagnosis.codeFix && <ResultCard icon={<GitBranch size={15} className="text-lime" />} title="Code Fix">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="muted" className="font-mono">{diagnosis.codeFix.file}</Badge>
            <Badge tone="muted" className="font-mono">{diagnosis.codeFix.lines}</Badge>
          </div>
          <div>
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-danger">
              <X size={12} /> Before
            </p>
            <CodeBlock code={diagnosis.codeFix.before} language={diagnosis.codeFix.language} tone="danger" label="Before" />
          </div>
          <div>
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-success">
              <Check size={12} /> After
            </p>
            <CodeBlock code={diagnosis.codeFix.after} language={diagnosis.codeFix.language} tone="success" label="After" />
          </div>
        </div>
      </ResultCard>}

      {diagnosis.alternatives.length > 0 && <section aria-label="Alternative fixes" className="rounded-xl border border-border bg-panel">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Scale size={15} className="text-warning" />
          <h3 className="text-sm font-semibold">Alternative Fixes</h3>
          <Badge tone="muted" className="ml-1">{diagnosis.alternatives.length}</Badge>
        </div>
        <div className="divide-y divide-border">
          {diagnosis.alternatives.map((alt) => (
            <details key={`${alt.title}:${alt.summary}`} className="group">
              <summary className="flex cursor-pointer list-none items-center gap-2.5 px-4 py-3 text-sm font-medium transition-colors hover:bg-white/[0.03] [&::-webkit-details-marker]:hidden">
                <ChevronRight size={14} className="text-muted transition-transform group-open:rotate-90" />
                {alt.title}
                <Badge tone="warning" className="ml-auto">tradeoff</Badge>
              </summary>
              <div className="ff-fade-up px-4 pb-3.5 pl-11">
                <p className="text-[13px] leading-relaxed text-foreground/80">{alt.summary}</p>
                <p className="mt-1.5 text-xs leading-relaxed text-warning/90">
                  <span className="font-medium">Tradeoff:</span> {alt.tradeoff}
                </p>
              </div>
            </details>
          ))}
        </div>
      </section>}

      <section aria-label="Evidence and sources" className="rounded-xl border border-border bg-panel">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <Search size={15} className="text-accent" />
          <h3 className="text-sm font-semibold">Evidence / Sources</h3>
          <Badge tone="accent" className="ml-1">{diagnosis.sources.length}</Badge>
          <div className="ml-auto flex flex-wrap gap-1" role="group" aria-label="Filter sources">
            {SOURCE_FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setSourceFilter(f.id)}
                aria-pressed={sourceFilter === f.id}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                  sourceFilter === f.id
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-border text-muted hover:border-border-strong hover:text-foreground"
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        <div className="divide-y divide-border">
          {visible.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted">No sources of this type.</p>
          ) : (
            visible.map((s) => {
              const open = expanded.includes(s.id);
              const sourceUrl = safeExternalUrl(s.url);
              return (
                <div key={s.id} className={cn("transition-colors hover:bg-white/[0.02]", s.used && "border-l-2 border-l-accent/60")}>
                  <button
                    onClick={() => toggleSource(s.id)}
                    aria-expanded={open}
                    className="flex w-full items-start gap-3 px-4 py-3 text-left"
                  >
                    <ChevronDown size={14} className={cn("mt-1 shrink-0 text-muted transition-transform", open && "rotate-180")} />
                    <span className={cn("mt-0.5 shrink-0", TYPE_TONE[s.type])}>{TYPE_ICON[s.type]}</span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-sm font-medium">{s.title}</span>
                        <span className="text-[11px] text-muted">{s.publisher}</span>
                      </span>
                      <span className="mt-0.5 flex items-center gap-2">
                        <span className="text-[11px] text-muted/70">{SOURCE_TYPE_LABEL[s.type]}</span>
                        {s.used && <Badge tone="lime">{diagnosis.answer ? "cited" : "retrieved"}</Badge>}
                      </span>
                    </span>
                    {s.relevance > 0 && <span className="flex shrink-0 flex-col items-end gap-1">
                      <span className="font-mono text-xs font-semibold text-foreground/80">{s.relevance}%</span>
                      <span className="h-1 w-16 overflow-hidden rounded-full bg-panel-2">
                        <span
                          className={cn("block h-full rounded-full", relevanceTone(s.relevance))}
                          style={{ width: `${s.relevance}%` }}
                        />
                      </span>
                    </span>}
                  </button>
                  {open && (
                    <div className="ff-fade-up pb-3.5 pl-[68px] pr-4">
                      <blockquote className="rounded-md border border-border bg-background px-3 py-2.5 font-mono text-xs leading-relaxed text-foreground/80">
                        “{s.excerpt}”
                      </blockquote>
                      {sourceUrl && (
                        <a
                          href={sourceUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-1.5 inline-flex items-center gap-1 text-xs text-accent transition-colors hover:text-accent-strong"
                        >
                          Open source <ExternalLink size={11} />
                        </a>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </section>

      <RagTransparency rag={diagnosis.rag} generation={diagnosis.generation} />
      <FollowUpChat
        sessionId={diagnosis.sessionId}
        confidence={diagnosis.confidence}
      />
    </div>
  );
}

function ResultCard({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="ff-fade-up rounded-xl border border-border bg-panel">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        {icon}
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <div className="px-4 py-4">{children}</div>
    </section>
  );
}
