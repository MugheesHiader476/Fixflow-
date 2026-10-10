"use client";

import { useState } from "react";
import {
  BookOpen,
  Code2,
  FileCode2,
  FolderGit2,
  GitBranch,
  MessageSquare,
  ExternalLink,
  X,
} from "lucide-react";
import type { Diagnosis, SourceType } from "@/lib/types";
import { SOURCE_TYPE_LABEL } from "@/lib/api";
import { cn, safeExternalUrl } from "@/lib/utils";

const TYPE_ICON: Record<SourceType, React.ReactNode> = {
  docs: <BookOpen size={13} />,
  github: <GitBranch size={13} />,
  community: <MessageSquare size={13} />,
  code: <Code2 size={13} />,
};
function SourceRow({
  source,
  compact = false,
}: {
  source: Diagnosis["sources"][number];
  compact?: boolean;
}) {
  const sourceUrl = safeExternalUrl(source.url);
  const content = (
    <>
      <div className="flex items-center gap-1.5 text-[11px] text-muted">
        <span
          className={cn(
            source.type === "docs" && "text-lime",
            source.type === "github" && "text-accent",
            source.type === "community" && "text-warning",
            source.type === "code" && "text-success"
          )}
        >
          {TYPE_ICON[source.type]}
        </span>
        {SOURCE_TYPE_LABEL[source.type]}
        {source.relevance > 0 && <span className="ml-auto font-mono text-muted/80">{source.relevance}%</span>}
        {sourceUrl && <ExternalLink size={11} className="opacity-0 transition-opacity group-hover:opacity-100" />}
      </div>
      <p className="mt-0.5 truncate text-[13px] text-foreground/90">{source.title}</p>
      {!compact && (
        <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted/80">
          {source.excerpt}
        </p>
      )}
    </>
  );
  const className = "group block rounded-md border border-transparent px-2.5 py-2 transition-colors hover:border-border hover:bg-white/[0.03]";
  return sourceUrl ? (
    <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className={className}>
      {content}
    </a>
  ) : (
    <div className={className}>{content}</div>
  );
}
export function RightPanel({
  diagnosis,
  open,
  onClose,
  files,
  repoUrl,
  techs,
}: {
  diagnosis: Diagnosis | null;
  open: boolean;
  onClose: () => void;
  files: string[];
  repoUrl: string;
  techs: string[];
}) {
  const [tab, setTab] = useState<"sources" | "context" | "project">("sources");
  if (!open) return null;
  return (
    <aside className="ff-fade-up flex h-full w-80 shrink-0 flex-col border-l border-border bg-surface max-lg:fixed max-lg:inset-y-0 max-lg:right-0 max-lg:z-40 max-lg:w-72 max-lg:shadow-2xl">
      <div className="flex h-14 items-center border-b border-border px-2">
        <div className="flex flex-1 items-center gap-1">
          {(
            [
              { id: "sources", label: "Sources", icon: <BookOpen size={13} /> },
              { id: "context", label: "Context", icon: <Code2 size={13} /> },
              { id: "project", label: "Project", icon: <FolderGit2 size={13} /> },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition-colors",
                tab === t.id ? "bg-accent/10 text-accent" : "text-muted hover:text-foreground"
              )}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
        <button
          onClick={onClose}
          aria-label="Close panel"
          className="rounded p-1.5 text-muted hover:text-foreground"
        >
          <X size={15} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "sources" &&
          (!diagnosis || !diagnosis.sources.length ? (
            <EmptyHint text="Ask a question to see retrieved sources here." />
          ) : (
            <div className="space-y-1">
              {diagnosis.sources.map((s) => (
                <SourceRow key={s.id} source={s} compact />
              ))}
            </div>
          ))}

        {tab === "context" && (
          <div className="space-y-3 text-sm">
            <MetaBlock label="Selected technologies" value={techs.length ? techs.join(" · ") : "—"} />
            <MetaBlock
              label="Error class"
              value={diagnosis?.detected.includes("asyncio") ? "RuntimeError / asyncio" : "—"}
            />
            <MetaBlock label="Environment" value="Not reported by backend" />
            <MetaBlock label="Session" value="Current workspace session" />
            {diagnosis && (
              <>
                {diagnosis.confidence !== null && <MetaBlock label="Confidence" value={`${diagnosis.confidence}%`} />}
                <MetaBlock label="Excerpts retrieved" value={String(diagnosis.rag.retrieved)} />
              </>
            )}
          </div>
        )}

        {tab === "project" && (
          <div className="space-y-3">
            <MetaBlock label="Repository" value={repoUrl || "No repository linked"} />
            <div>
              <p className="mb-1.5 text-[11px] uppercase tracking-wider text-muted/70">
                Files ({files.length})
              </p>
              {files.length === 0 ? (
                <EmptyHint text="No files attached to this session yet." />
              ) : (
                <ul className="space-y-1">
                  {files.map((f, index) => (
                    <li
                      key={`${f}-${index}`}
                      className="flex items-center gap-2 rounded-md bg-panel px-2.5 py-1.5 text-[13px] text-foreground/85"
                    >
                      <FileCode2 size={13} className="shrink-0 text-muted" />
                      <span className="truncate font-mono text-xs">{f}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function MetaBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-panel px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wider text-muted/70">{label}</p>
      <p className="mt-0.5 text-[13px] text-foreground/90">{value}</p>
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted">
      {text}
    </div>
  );
}
