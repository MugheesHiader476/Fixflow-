"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { WorkspaceHero, WorkspaceFeatures } from "@/components/layout/workspace-intro";
import { RightPanel } from "@/components/layout/right-panel";
import { DebugInput } from "@/components/debug/debug-input";
import { DiagnosisResult } from "@/components/debug/diagnosis-result";
import { AgentPanel } from "@/components/workspace/agent-panel";
import { ArrowUpRight, BookOpen, Plug, Upload } from "lucide-react";
import Link from "next/link";
import { useToast } from "@/components/ui/toast";
import { getSession, saveSolution as saveSolutionApi } from "@/lib/api";
import type { Diagnosis } from "@/lib/types";

function DebugSessionContent({ sessionId, chatId }: { sessionId: string | null; chatId: string | null }) {
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [loadingSession, setLoadingSession] = useState(Boolean(sessionId));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [techs, setTechs] = useState<string[]>([]);
  const [repoUrl, setRepoUrl] = useState("");
  const [rightOpen, setRightOpen] = useState(false);
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
      sessionTitle={diagnosis ? `${diagnosis.detected.slice(0, 2).join(" · ") || "Knowledge"} session` : "Workspace chat"}
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
      onToggleRightPanel={sessionId ? () => setRightOpen((o) => !o) : undefined}
    >
      <div className="ff-home">
        {!sessionId && <section id="workspace" className="ff-start" aria-labelledby="workspace-title">
          <div className="ff-workspace-status"><span />Your private knowledge workspace</div>
          <div className="ff-start-heading"><span className="ff-greeting-mark" aria-hidden="true"><ArrowUpRight size={27} /></span><h1 id="workspace-title">Your knowledge, together.</h1></div>
          <p className="ff-start-description">Ask once. Find answers across your apps.</p>
          <AgentPanel initialMessageId={chatId} onMessageChange={(id) => router.replace(id ? `/?chat=${encodeURIComponent(id)}` : "/", { scroll: false })} />
          <nav className="ff-starter-actions" aria-label="Get started">
            <Link href="/sources"><Upload size={15} />Upload a file</Link>
            <Link href="/connectors"><Plug size={15} />Connect an app</Link>
            <Link href="/sources"><BookOpen size={15} />Explore your sources</Link>
          </nav>
          <p className="ff-current-capability">Your uploads and selected app data, together in one conversation.</p>
        </section>}
        {sessionId && <section className="ff-workspace-section" aria-labelledby="session-heading">
          <div className="ff-section-heading"><div><p className="ff-eyebrow">YOUR CONVERSATION</p><h1 id="session-heading">Review the conversation.</h1></div></div>
          {loadingSession && <p role="status" className="text-sm text-muted">Loading session…</p>}
          {!loadingSession && <DebugInput
            disabled busy={false} onFilesChange={setFiles} onTechsChange={setTechs} onRepoChange={setRepoUrl}
            initialValues={diagnosis?.request ? {
              ...diagnosis.request, question: diagnosis.request.question ?? undefined,
              error: diagnosis.request.error ?? undefined, code: diagnosis.request.code ?? undefined,
              context: diagnosis.request.context ?? undefined, repoUrl: diagnosis.request.repo_url ?? undefined,
            } : undefined}
            onClear={() => { setDiagnosis(null); setError(null); setSaved(false); router.replace("/"); }}
          />}
          <p className="ff-input-note">This historical conversation is preserved. <Link href="/">Start a new chat</Link> to ask about your workspace.</p>
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
        </section>}
        {!sessionId && <><WorkspaceHero /><WorkspaceFeatures /></>}
      </div>
    </AppShell>
  );
}

function SessionRoute() {
  const parameters = useSearchParams();
  const sessionId = parameters.get("session");
  const chatId = parameters.get("chat");
  return <DebugSessionContent key={sessionId ?? chatId ?? "new"} sessionId={sessionId} chatId={chatId} />;
}

export default function DebugSessionPage() {
  return (
    <Suspense fallback={null}>
      <SessionRoute />
    </Suspense>
  );
}
