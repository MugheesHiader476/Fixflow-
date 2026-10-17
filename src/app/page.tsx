"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { WorkspaceHero, WorkspaceGuide, WorkspaceFeatures } from "@/components/layout/workspace-intro";
import { RightPanel } from "@/components/layout/right-panel";
import { DebugInput } from "@/components/debug/debug-input";
import { DiagnosisResult } from "@/components/debug/diagnosis-result";
import { WorkspaceReadiness } from "@/components/debug/workspace-readiness";
import { useToast } from "@/components/ui/toast";
import { getSession, saveSolution as saveSolutionApi } from "@/lib/api";
import type { Diagnosis } from "@/lib/types";

function DebugSessionContent({ sessionId }: { sessionId: string | null }) {
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [loadingSession, setLoadingSession] = useState(Boolean(sessionId));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [techs, setTechs] = useState<string[]>([]);
  const [repoUrl, setRepoUrl] = useState("");
  const [rightOpen, setRightOpen] = useState(false);
  const SessionHeading = sessionId ? "h1" : "h2";
  const { toast } = useToast();
  const router = useRouter();

  useEffect(() => {
    const sid = sessionId;
    if (sid) {
      const controller = new AbortController();
      void getSession(sid, controller.signal)
        .then((session) => {
          if (controller.signal.aborted) return;
          setDiagnosis(session);
          setTechs(session.detected);
          setFiles(session.request?.files?.map((file) => file.name) ?? []);
          setRepoUrl(session.request?.repo_url ?? "");
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setError("Could not reopen that debug session.");
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoadingSession(false);
        });
      return () => controller.abort();
    }
  }, [sessionId]);

  const saveSolution = async () => {
    if (!diagnosis || saving || saved) return;
    setSaving(true);
    try {
      await saveSolutionApi({
        problem: (diagnosis.request?.question || diagnosis.request?.error || diagnosis.request?.context || diagnosis.rootCause).slice(0, 20_000),
        rootCause: diagnosis.rootCause,
        technology: diagnosis.detected,
        fixSummary: diagnosis.answer?.text ?? diagnosis.recommendedFix[0]?.detail ?? "Review the retrieved evidence.",
        sources: diagnosis.answer?.citations ?? diagnosis.sources.map((source) => ({
          id: source.id, title: source.title, type: source.type, source_id: source.source_id,
          url: source.url, excerpt: source.excerpt, location: source.location,
        })),
      });
      toast("Answer saved to Saved Answers", "success");
      setSaved(true);
    } catch {
      toast("Could not save this solution.", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell
      sessionTitle={diagnosis ? `${diagnosis.detected.slice(0, 2).join(" · ") || "Knowledge"} session` : "Parsing workspace"}
      techs={techs.length ? techs : (diagnosis?.detected ?? [])}
      rightPanel={
        <RightPanel
          diagnosis={diagnosis}
          open={rightOpen}
          onClose={() => setRightOpen(false)}
          files={files}
          repoUrl={repoUrl}
          techs={techs.length ? techs : (diagnosis?.detected ?? [])}
        />
      }
      rightPanelOpen={rightOpen}
      onToggleRightPanel={() => setRightOpen((o) => !o)}
    >
      <div className="ff-home">
        {!sessionId && <WorkspaceHero />}
        {!sessionId && <section className="ff-intro"><p className="ff-eyebrow"><span aria-hidden="true" />YOUR KNOWLEDGE, WITH EVIDENCE</p><div><h2>Your sources.<br />Their original structure.</h2><p>Upload documents or connect an authorized app. Content is parsed and stored privately with its source locations.</p></div></section>}
        <section id="workspace" className="ff-workspace-section" aria-labelledby="session-heading">
          <div className="ff-section-heading"><div><p className="ff-eyebrow">{sessionId ? "YOUR CONVERSATION" : "YOUR WORKSPACE"}</p><SessionHeading id="session-heading">{sessionId ? "Review the conversation." : "Prepare your sources."}</SessionHeading></div><span className="ff-mode-label"><span />Private document parsing</span></div>
          <div className={sessionId ? "ff-session-input" : "ff-workspace-grid"}>
            <div className="min-w-0">
        {!sessionId && <WorkspaceReadiness />}
        {loadingSession && <p role="status" className="text-sm text-muted">Loading session…</p>}
        {!loadingSession && <DebugInput
          disabled
          busy={false}
          onFilesChange={setFiles}
          onTechsChange={setTechs}
          onRepoChange={setRepoUrl}
          initialValues={diagnosis?.request ? {
            ...diagnosis.request,
            question: diagnosis.request.question ?? undefined,
            error: diagnosis.request.error ?? undefined,
            code: diagnosis.request.code ?? undefined,
            context: diagnosis.request.context ?? undefined,
            repoUrl: diagnosis.request.repo_url ?? undefined,
          } : undefined}
          onClear={() => { setDiagnosis(null); setError(null); setSaved(false); router.replace("/"); }}
        />}

              <p className="ff-input-note">Questions, search, and AI answers have been removed. Existing sessions remain available in History.</p>
            </div>
            {!sessionId && <WorkspaceGuide />}
          </div>
        <div className="ff-results">

          {error && (
            <div
              className="ff-fade-up rounded-xl border border-danger/40 bg-danger/10 px-4 py-3.5 text-sm text-danger"
              role="alert"
            >
              {error}
              <button
                onClick={() => setError(null)}
                className="ml-3 underline underline-offset-2 hover:no-underline"
              >
                Dismiss
              </button>
            </div>
          )}
          {!loadingSession && !error && !diagnosis && (
            <div className="rounded-xl border border-dashed border-border px-6 py-10 text-center">
              <p className="text-sm text-muted">
                Upload a source on Knowledge Sources to inspect and parse its content.
              </p>
              <p className="mt-1 font-mono text-[11px] text-muted/60">
                parsing only · no chunking or model inference
              </p>
            </div>
          )}
          {diagnosis && (
            <DiagnosisResult
              key={diagnosis.sessionId}
              diagnosis={diagnosis}
              onSaved={saveSolution}
              saving={saving}
              saved={saved}
            />
          )}
        </div>
        </section>
        {!sessionId && <WorkspaceFeatures />}
      </div>
    </AppShell>
  );
}

function SessionRoute() {
  const sessionId = useSearchParams().get("session");
  return <DebugSessionContent key={sessionId ?? "new"} sessionId={sessionId} />;
}

export default function DebugSessionPage() {
  return (
    <Suspense fallback={null}>
      <SessionRoute />
    </Suspense>
  );
}
