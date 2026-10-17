"use client";

import Link from "next/link";
import { checkBackendHealth, listKnowledgeSources } from "@/lib/api";
import { useResource } from "@/lib/use-resource";

export function WorkspaceReadiness() {
  const sources = useResource(listKnowledgeSources);
  const health = useResource(checkBackendHealth);
  if (sources.loading || health.loading) return <p role="status" className="text-xs text-muted">Checking your workspace…</p>;
  if (sources.error || health.error) return <p role="alert" className="text-sm text-warning">Could not check workspace readiness. <button className="underline" onClick={() => { sources.reload(); health.reload(); }}>Retry</button></p>;
  return (
    <div className="mb-3 space-y-2 rounded-lg border border-border bg-panel p-3 text-sm">
      <p>Parsing only. Upload documents or connect an authorized app to preserve their content and structure.</p>
      <p className="text-xs text-warning">Chunking, embeddings, source search, and AI answers have been removed. History and saved findings remain available.</p>
      <div className="flex flex-wrap gap-3 text-xs text-accent"><Link href="/sources">Upload documents or data</Link><Link href="/connectors">Connect an app</Link></div>
    </div>
  );
}
