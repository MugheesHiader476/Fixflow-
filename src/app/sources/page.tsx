"use client";

import { useEffect, useRef, useState } from "react";
import { BookOpen, FileText, GitBranch, Link2, Upload, Database } from "lucide-react";
import { PageHeading } from "@/components/layout/page-heading";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import {
  addKnowledgeSource,
  listKnowledgeSources,
} from "@/lib/api";
import type { KnowledgeSource } from "@/lib/types";
import { isSourcePending, mergeSources, SOURCE_STATUS } from "@/lib/sources";
import { cn } from "@/lib/utils";

type SourceMode = "docs" | "github" | "upload";

const SOURCE_MODES: {
  id: SourceMode;
  label: string;
  description: string;
  icon: typeof BookOpen;
}[] = [
  {
    id: "docs",
    label: "Paste documentation",
    description: "Add a guide, runbook, or internal reference.",
    icon: BookOpen,
  },
  {
    id: "github",
    label: "Documentation URL",
    description: "Coming later. Upload a file or paste text for now.",
    icon: Link2,
  },
  {
    id: "upload",
    label: "Upload a file",
    description: "PDF, Markdown, TXT, RST, CSV, HTML, or DOCX.",
    icon: Upload,
  },
];

function sourceName(mode: SourceMode, title: string, value: string): string {
  if (mode === "github") return value.trim();
  if (title) return title;
  return mode === "docs" ? "Pasted documentation" : "Pasted document";
}

export default function SourcesPage() {
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [mode, setMode] = useState<SourceMode>("docs");
  const [title, setTitle] = useState("");
  const [value, setValue] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const uploadsVersion = useRef(0);
  const uploadRequest = useRef<AbortController | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();

  useEffect(() => {
    const controller = new AbortController();
    const version = uploadsVersion.current;
    void listKnowledgeSources(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        if (version === uploadsVersion.current) setSources(items);
        else setSources((current) => mergeSources(items, current));
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setLoadError("Could not load knowledge sources.");
        }
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refreshKey]);

  useEffect(() => () => uploadRequest.current?.abort(), []);

  const hasPendingSources = sources.some(isSourcePending);
  useEffect(() => {
    if (!hasPendingSources) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      const version = uploadsVersion.current;
      try {
        const current = await listKnowledgeSources(controller.signal);
        if (controller.signal.aborted) return;
        if (version === uploadsVersion.current) setSources(current);
        setLoadError(null);
      } catch {
        if (!controller.signal.aborted) setLoadError("Could not refresh source status. Retrying…");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 2000);
      }
    };
    timer = setTimeout(refresh, 2000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [hasPendingSources]);

  const resetForm = () => {
    setTitle("");
    setValue("");
    setSelectedFile(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const selectMode = (nextMode: SourceMode) => {
    setMode(nextMode);
    resetForm();
  };

  const submitSource = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if ((!value.trim() && !selectedFile) || busy || uploadRequest.current) return;

    const controller = new AbortController();
    uploadRequest.current = controller;
    uploadsVersion.current += 1;
    setBusy(true);
    try {
      const source = await addKnowledgeSource({
        kind: mode,
        value: sourceName(mode, title, value),
        content: mode === "docs" || (mode === "upload" && !selectedFile) ? value : undefined,
        file: mode === "upload" ? selectedFile ?? undefined : undefined,
      }, controller.signal);
      if (controller.signal.aborted) return;
      uploadsVersion.current += 1;
      setSources((current) => [source, ...current.filter((item) => item.id !== source.id)]);
      resetForm();
      toast(source.error_message || "Source saved to the knowledge base.", source.status === "failed" ? "error" : "success");
    } catch (error) {
      if (!controller.signal.aborted) toast(error instanceof Error ? error.message : "Could not add this source. Try again.", "error");
    } finally {
      uploadRequest.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const handleFile = (file: File | undefined) => {
    if (!file) return;
    if (!/\.(md|txt|rst|pdf|docx|csv|html?)$/i.test(file.name)) {
      toast("Choose a supported document type.", "error");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      toast("Document exceeds the 50 MB limit", "error");
      return;
    }
    setTitle(file.name);
    setSelectedFile(file);
    setValue("");
  };

  return (
    <AppShell sessionTitle="Knowledge Sources" techs={["Documentation"]}>
      <div className="ff-page">
        <PageHeading eyebrow="YOUR KNOWLEDGE, CONNECTED" title="Good context starts here." action={<span className="ff-source-count"><Database size={16} />{sources.length} sources available</span>}>
          Your guides, runbooks, and reference docs. Bring them together and make them part of your next investigation.
        </PageHeading>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.8fr)]">
          <section className="rounded-xl border border-border bg-panel p-4 sm:p-5">
            <div className="mb-4">
              <h2 className="text-sm font-semibold">Add documentation</h2>
              <p className="mt-1 text-xs text-muted">Upload or paste a document to make it available for keyword search.</p>
            </div>

            <div className="grid gap-1.5 sm:grid-cols-3" role="tablist" aria-label="Source type">
              {SOURCE_MODES.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="tab"
                    aria-selected={mode === item.id}
                    disabled={busy || item.id === "github"}
                    onClick={() => selectMode(item.id)}
                    className={cn(
                      "flex items-start gap-2 rounded-md border px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                      mode === item.id
                        ? "border-accent/50 bg-accent/10 text-foreground"
                        : "border-border text-muted hover:border-border-strong hover:text-foreground"
                    )}
                  >
                    <Icon size={15} className={mode === item.id ? "mt-0.5 text-accent" : "mt-0.5"} />
                    <span>
                      <span className="block text-xs font-medium">{item.label}</span>
                      <span className="mt-0.5 block text-[10px] leading-snug text-muted">{item.description}</span>
                    </span>
                  </button>
                );
              })}
            </div>

            <form onSubmit={submitSource} className="mt-5 space-y-3">
              <fieldset disabled={busy} className="min-w-0 space-y-3">
              {mode !== "github" && (
                <label className="block text-xs font-medium text-foreground/80">
                  {mode === "docs" ? "Document title" : "File"}
                  {mode === "docs" ? (
                    <input
                      value={title}
                      maxLength={255}
                      onChange={(event) => setTitle(event.target.value)}
                      placeholder="e.g. Internal debugging runbook"
                      className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground placeholder:text-muted/50 focus:border-accent/60 focus:outline-none"
                    />
                  ) : (
                    <div className="mt-1.5 flex gap-2">
                      <input
                        readOnly
                        value={title}
                        placeholder="Choose a .md, .txt, or .pdf file"
                        className="h-10 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm text-muted placeholder:text-muted/50"
                      />
                      <Button type="button" size="md" variant="outline" onClick={() => fileRef.current?.click()}>
                        <FileText size={14} />
                        Browse
                      </Button>
                      <input
                        ref={fileRef}
                        type="file"
                        accept=".md,.txt,.rst,.pdf,.docx,.csv,.html,.htm"
                        aria-label="Upload document"
                        onChange={(event) => handleFile(event.target.files?.[0])}
                        className="hidden"
                      />
                    </div>
                  )}
                </label>
              )}

              {mode === "github" && (
                <label className="block text-xs font-medium text-foreground/80">
                  Documentation or repository URL
                  <input
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    placeholder="https://docs.example.com or https://github.com/org/repo"
                    type="url"
                    maxLength={2048}
                    className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground placeholder:text-muted/50 focus:border-accent/60 focus:outline-none"
                    required
                  />
                </label>
              )}
              {mode !== "github" && selectedFile && (
                <div className="rounded-md border border-border p-3 text-xs text-muted">
                  <p>{selectedFile.name} · {Math.ceil(selectedFile.size / 1024)} KB · ready to upload</p>
                  <Button type="button" variant="ghost" size="sm" onClick={resetForm}>Remove file</Button>
                </div>
              )}
              {mode !== "github" && !selectedFile && (
                <label className="block text-xs font-medium text-foreground/80">
                  {mode === "docs" ? "Documentation content" : "Paste document text"}
                  <textarea
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    placeholder={mode === "docs" ? "Paste the documentation, runbook, or troubleshooting guide here…" : "Choose a file above or paste its text here…"}
                    rows={10}
                    className="mt-1.5 w-full resize-y rounded-md border border-border bg-background px-3 py-2.5 font-mono text-xs leading-relaxed text-foreground placeholder:font-sans placeholder:text-muted/50 focus:border-accent/60 focus:outline-none"
                    required
                  />
                </label>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
                <p className="text-[11px] text-muted">Files up to 50 MB. Scanned PDFs need OCR first.</p>
                <Button type="submit" variant="primary" loading={busy} disabled={!selectedFile && !value.trim()}>
                  <Upload size={14} />
                  Add to knowledge base
                </Button>
              </div>
              </fieldset>
            </form>
          </section>

          <section className="rounded-xl border border-border bg-panel p-4 sm:p-5">
            <div className="mb-3 flex items-center gap-2">
              <BookOpen size={15} className="text-lime" />
              <h2 className="text-sm font-semibold">Knowledge sources</h2>
              <Button className="ml-auto" size="sm" loading={loading} onClick={() => {
                setLoading(true); setLoadError(null); setRefreshKey((value) => value + 1);
              }}>Refresh</Button>
            </div>
            <div className="space-y-1.5">
              {loading && <p role="status" className="text-xs text-muted">Loading sources…</p>}
              {loadError && <p role="alert" className="text-xs text-danger">{loadError}</p>}
              {sources.map((source) => (
                <div key={source.id} className="rounded-md border border-border bg-background px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    {source.kind === "github" ? <GitBranch size={14} className="mt-0.5 text-accent" /> : <FileText size={14} className="mt-0.5 text-muted" />}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium">{source.name}</p>
                      <p className="mt-0.5 text-[10px] text-muted">
                        {source.kind} · {source.document_count} documents · {source.chunk_count} chunks
                      </p>
                      {source.error_message && <p className="mt-1 text-[10px] text-red-400">{source.error_message}</p>}
                    </div>
                    <Badge tone={SOURCE_STATUS[source.status].tone}>{SOURCE_STATUS[source.status].label}</Badge>
                  </div>
                </div>
              ))}
              {!loading && !loadError && sources.length === 0 && <p className="py-8 text-center text-xs text-muted">No sources added yet.</p>}
            </div>
          </section>
        </div>
      </div>
    </AppShell>
  );
}
