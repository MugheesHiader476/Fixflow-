"use client";

import Link from "next/link";
import { checkBackendHealth, listKnowledgeSources } from "@/lib/api";
import { useResource } from "@/lib/use-resource";

export function WorkspaceReadiness() {
  const sources = useResource(listKnowledgeSources);
  const health = useResource(checkBackendHealth);
  if (sources.loading || health.loading) return <p role="status" className="text-xs text-muted">Checking your workspace…</p>;
  if (sources.error || health.error) return <p role="alert" className="text-sm text-warning">Could not check workspace readiness. <button className="underline" onClick={() => { sources.reload(); health.reload(); }}>Retry</button></p>;
  const ready = (sources.data ?? []).filter((source) => source.retrieval_available !== false && source.is_active !== false
    && ["ready_for_embedding", "embedding", "indexed"].includes(source.status));
  const readyLabel = `${ready.length} source${ready.length === 1 ? "" : "s"} available for questions.`;
  return (
    <div className="mb-3 space-y-2 rounded-lg border border-border bg-panel p-3 text-sm">
      <p>{ready.length ? readyLabel : "Start by adding a source. Your documents and connected apps stay private to your account."}</p>
      {health.data?.ai_generation !== "configured" && <p className="text-xs text-warning">AI answers are not configured by the workspace operator. Source search is available.</p>}
      {health.data?.answer_service === "unavailable" && <p className="text-xs text-warning">The local answer service is currently unavailable. Please try again later.</p>}
      <div className="flex flex-wrap gap-3 text-xs text-accent"><Link href="/sources">Upload documents or data</Link><Link href="/connectors">Connect an app</Link></div>
    </div>
  );
}
