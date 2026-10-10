import type { Diagnosis } from "./types";

export function diagnosisMarkdown(diagnosis: Diagnosis): string {
  if (diagnosis.answer) {
    return ["# FixFlow answer", "", diagnosis.answer.text, "", "## Evidence", "",
      ...diagnosis.answer.citations.map((citation) => {
        const location = citation.location ? " — " + citation.location : "";
        const url = citation.url ? "\n\n" + citation.url : "";
        return `[${citation.number}] ${citation.title}${location}\n\n> ${citation.quote.replaceAll("\n", "\n> ")}${url}`;
      }),
    ].join("\n");
  }
  const confidence = diagnosis.confidence === null ? "Not assessed" : `${diagnosis.confidence}%`;
  const steps = diagnosis.recommendedFix
    .map((step, index) => `${index + 1}. **${step.title}** — ${step.detail}`)
    .join("\n");
  const sources = diagnosis.sources
    .map((source) => {
      const location = source.url ? ` — ${source.url}` : "";
      return `- ${source.title}${location}`;
    })
    .join("\n");

  return [
    "# FixFlow diagnosis",
    "",
    `**Status:** ${diagnosis.status}`,
    `**Confidence:** ${confidence}`,
    "",
    "## Root cause",
    "",
    diagnosis.rootCause,
    "",
    "## Why this happens",
    "",
    diagnosis.whyThisHappens,
    "",
    "## Recommended fix",
    "",
    steps,
    ...(diagnosis.codeFix ? [
      "", "## Code fix", "", diagnosis.codeFix.file,
      "", "### Before", "", `\`\`\`${diagnosis.codeFix.language}`, diagnosis.codeFix.before, "\`\`\`",
      "", "### After", "", `\`\`\`${diagnosis.codeFix.language}`, diagnosis.codeFix.after, "\`\`\`",
    ] : []),
    "",
    "## Sources",
    "",
    sources || "No sources were returned.",
  ].join("\n");
}
