"use client";

import {
  CheckCircle2,
  CircleAlert,
  Database,
  ExternalLink,
  Moon,
  Server,
  Sun,
} from "lucide-react";
import {
  Show,
  SignInButton,
  SignUpButton,
  UserButton,
} from "@clerk/nextjs";
import { PageHeading } from "@/components/layout/page-heading";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/components/layout/theme-provider";
import { checkBackendHealth, listKnowledgeSources } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import { SOURCE_STATUS } from "@/lib/sources";

const BACKEND_TONE = {
  checking: "warning",
  online: "success",
  offline: "danger",
  degraded: "warning",
} as const;

export default function SettingsPage() {
  const { dark, toggle } = useTheme();
  const health = useResource(checkBackendHealth);
  const knowledge = useResource(listKnowledgeSources);
  const sources = knowledge.data ?? [];
  let backend: keyof typeof BACKEND_TONE = "degraded";
  if (health.loading) backend = "checking";
  else if (health.error) backend = "offline";
  else if (health.data?.status === "ok") backend = "online";

  return (
    <AppShell sessionTitle="Settings">
      <div className="ff-page">
        <PageHeading eyebrow="MAKE IT YOURS" title="Your workspace. Your way.">
          Adjust your appearance, check your connections, and see what’s ready to work.
        </PageHeading>

        <section className="rounded-xl border border-border bg-panel p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-accent/10 text-accent">
              {dark ? <Moon size={16} /> : <Sun size={16} />}
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">Appearance</h2>
              <p className="mt-1 text-xs text-muted">Current theme: {dark ? "Dark" : "Light"}</p>
            </div>
            <Button variant="outline" size="sm" onClick={toggle}>
              {dark ? <Sun size={14} /> : <Moon size={14} />}
              Switch to {dark ? "light" : "dark"}
            </Button>
          </div>
        </section>

        <section className="rounded-xl border border-border bg-panel p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-success/10 text-success">
              <Server size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">Backend connection</h2>
              <p className="mt-1 text-xs text-muted">FastAPI health endpoint</p>
            </div>
            <Badge tone={BACKEND_TONE[backend]}>
              {backend}
            </Badge>
          </div>
          <div className="mt-4 flex items-center justify-between gap-3 border-t border-border pt-3">
            <p className="font-mono text-[11px] text-muted">{process.env.NEXT_PUBLIC_API_URL || "Backend URL not configured"}</p>
            <Button variant="outline" size="sm" onClick={() => { health.reload(); knowledge.reload(); }} loading={health.loading}>
              Check connection
            </Button>
          </div>
          {backend === "offline" && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-danger">
              <CircleAlert size={13} /> Start FastAPI to enable documentation search and history.
            </p>
          )}
          {backend === "online" && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-success">
              <CheckCircle2 size={13} /> FastAPI is reachable.
            </p>
          )}
          {backend === "degraded" && <p role="alert" className="mt-3 text-xs text-warning">The API is reachable, but database readiness checks failed.</p>}
          {health.data && (
            <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-3 text-xs">
              <dt className="text-muted">Database / pgvector</dt><dd>{health.data.database} / {health.data.pgvector}</dd>
              <dt className="text-muted">Schema</dt><dd>{health.data.schema} (revision {health.data.revision ?? "unknown"})</dd>
              <dt className="text-muted">Documents / chunks</dt><dd>{health.data.documents ?? "—"} / {health.data.chunks ?? "—"}</dd>
              <dt className="text-muted">Embedded chunks</dt><dd>{health.data.embedded_chunks ?? "—"}</dd>
              <dt className="text-muted">Embedding configuration</dt><dd>{health.data.embedding_configured ? "Configured" : "Not configured"}</dd>
              <dt className="text-muted">AI generation</dt><dd>{health.data.ai_generation === "configured" ? "Configured" : "Not connected"}</dd>
            </dl>
          )}
        </section>

        <section className="rounded-xl border border-border bg-panel p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-lime/10 text-lime">
              <Database size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">Knowledge collection</h2>
              <p className="mt-1 text-xs text-muted">Sources available to the retrieval service</p>
            </div>
            <Badge tone="lime">{sources.length} sources</Badge>
          </div>
          <div className="mt-4 space-y-2 border-t border-border pt-3">
            {sources.map((source) => (
              <div key={source.id} className="flex items-center gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-foreground/80">{source.name}</span>
                <span className="font-mono text-muted">{source.chunks} records</span>
                <Badge tone={SOURCE_STATUS[source.status].tone}>{SOURCE_STATUS[source.status].label}</Badge>
              </div>
            ))}
            {knowledge.loading && <p role="status" className="text-xs text-muted">Loading source status…</p>}
            {knowledge.error && <p role="alert" className="text-xs text-danger">{knowledge.error}</p>}
            {!knowledge.loading && !knowledge.error && !sources.length && <p className="text-xs text-muted">No documents yet. Upload documentation in Knowledge Sources to enable retrieval.</p>}
          </div>
        </section>

        <section className="rounded-xl border border-border bg-panel p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">Account</h2>
              <p className="mt-1 text-xs text-muted">Manage your Clerk sign-in. This development workspace does not yet isolate backend data by user.</p>
            </div>
            <Show when="signed-in">
              <UserButton />
            </Show>
            <Show when="signed-out">
              <div className="flex gap-2">
                <SignInButton mode="modal"><Button variant="outline" size="sm">Sign in</Button></SignInButton>
                <SignUpButton mode="modal"><Button variant="primary" size="sm">Sign up</Button></SignUpButton>
              </div>
            </Show>
          </div>
          <a href="https://dashboard.clerk.com" target="_blank" rel="noopener noreferrer" className="mt-4 inline-flex items-center gap-1.5 text-xs text-muted hover:text-foreground">
            Open Clerk dashboard <ExternalLink size={12} />
          </a>
        </section>
      </div>
    </AppShell>
  );
}
