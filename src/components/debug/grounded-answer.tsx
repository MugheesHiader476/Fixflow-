"use client";

import type { GroundedAnswer } from "@/lib/types";
import { safeExternalUrl } from "@/lib/utils";

export function GroundedAnswerView({ answer }: { answer: GroundedAnswer }) {
  return (
    <section aria-label="Evidence-grounded answer" className="rounded-xl border border-border bg-panel p-4 sm:p-5">
      <h3 className="text-sm font-semibold">{answer.status === "answered" ? "Answer" : "More evidence needed"}</h3>
      <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/90">{answer.text}</p>
      {answer.citations.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-border pt-3" aria-label="Answer citations">
          {answer.citations.map((citation) => {
            const url = safeExternalUrl(citation.url ?? "");
            return (
              <details key={citation.number} className="rounded-lg border border-border bg-background p-3">
                <summary className="cursor-pointer break-words text-sm text-accent">
                  [{citation.number}] {citation.title}
                </summary>
                {citation.location && <p className="mt-2 break-words text-xs text-muted">{citation.location}</p>}
                <blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 border-accent/40 pl-3 text-xs leading-relaxed">{citation.quote}</blockquote>
                {url && <a href={url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-xs text-accent">Open original source</a>}
              </details>
            );
          })}
        </div>
      )}
    </section>
  );
}
