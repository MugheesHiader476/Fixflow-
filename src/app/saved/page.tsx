"use client";

import { Bookmark, ChevronDown } from "lucide-react";
import { PageHeading } from "@/components/layout/page-heading";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { listSaved } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import { Button } from "@/components/ui/button";
import { safeExternalUrl } from "@/lib/utils";

export default function SavedPage() {
  const { data, loading, error, reload } = useResource(listSaved);
  const solutions = data ?? [];

  return (
    <AppShell sessionTitle="Saved Answers">
      <div className="ff-page">
        <PageHeading eyebrow="YOUR PERSONAL REFERENCE" title="Keep what works." action={<Button size="sm" onClick={reload} loading={loading}>Refresh answers</Button>}>
          Your saved answers and quoted evidence, ready when you need them.
        </PageHeading>
        {error && <p className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger">{error}</p>}
        {loading && <p role="status" className="text-sm text-muted">Loading saved answers…</p>}
        <section className="grid gap-3 md:grid-cols-2">
          {solutions.map((solution) => (
            <article key={solution.id} className="rounded-lg border border-border bg-panel p-4">
              <div className="flex items-start gap-2">
                <Bookmark size={16} className="mt-0.5 shrink-0 text-lime" />
                <div className="min-w-0 flex-1">
                  <h2 className="text-sm font-medium">{solution.problem}</h2>
                  <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-muted">{solution.fixSummary}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {solution.technology.map((technology) => <Badge key={technology} tone="muted">{technology}</Badge>)}
                  </div>
                </div>
              </div>
              <details className="mt-3 border-t border-border pt-3">
                <summary className="flex cursor-pointer items-center gap-2 text-xs text-accent">
                  View saved details <ChevronDown size={14} />
                </summary>
                <p className="mt-3 whitespace-pre-wrap text-sm">{solution.rootCause}</p>
                <p className="mt-2 text-xs text-muted">Saved {new Date(solution.savedAt).toLocaleString()}</p>
                {solution.sources.length > 0 && <ul className="mt-2 space-y-1 text-xs text-muted">
                  {solution.sources.map((source, index) => {
                    const url = safeExternalUrl(source.url ?? "");
                    return <li key={`${source.id ?? source.title}:${source.number ?? index}`}>
                      <details className="rounded border border-border p-2">
                        <summary className="cursor-pointer">{source.number && `[${source.number}] `}{source.title}</summary>
                        {source.location && <p className="mt-1">{source.location}</p>}
                        {(source.quote || source.excerpt) && <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-accent/40 pl-2">{source.quote || source.excerpt}</blockquote>}
                        {url && <a href={url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-accent">Open original source</a>}
                      </details>
                    </li>;
                  })}
                </ul>}
              </details>
            </article>
          ))}
          {!loading && !solutions.length && !error && <p className="col-span-full rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted">No saved answers yet.</p>}
        </section>
      </div>
    </AppShell>
  );
}
