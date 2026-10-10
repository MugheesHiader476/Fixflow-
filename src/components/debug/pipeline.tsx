"use client";

import { Loader2 } from "lucide-react";

export function PipelineProgress() {
  return (
    <section aria-label="Answer preparation" aria-busy="true" className="rounded-xl border border-border bg-panel p-5">
      <p role="status" className="flex items-center gap-2 text-sm">
        <Loader2 size={16} className="ff-spin text-accent" />
        Searching your sources and preparing the answer…
      </p>
    </section>
  );
}
