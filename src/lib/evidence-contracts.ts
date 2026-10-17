function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export interface EvidenceSpan {
  block_id: string;
  start: number;
  end: number;
  unit: "characters" | "rows";
  provenance: {
    page: number | null;
    line_start: number | null;
    line_end: number | null;
    sheet: string | null;
    cell_range: string | null;
  };
}

export interface EvidencePassage {
  id: string;
  source_id: string;
  source_hash: string;
  canonical_hash: string;
  ordinal: number;
  kind: "prose" | "email" | "slack" | "code" | "table";
  prefix: string;
  content: string;
  tokens: number;
  spans: EvidenceSpan[];
  warnings: string[];
}

export interface EmbeddingReceipt {
  source_id: string;
  prepared: number;
  remaining: number;
  embedding_calls: number;
  input_tokens: number | null;
  elapsed_ms: number;
  status: "not_queued" | "queued" | "running" | "complete" | "failed" | "cancelled";
  active: boolean;
  error: string | null;
}

export interface EvidenceAnswer {
  status: "answered" | "evidence_only" | "insufficient_evidence" | "tool_required" | "generation_unavailable";
  text: string;
  claims: { text: string; citations: { passage_id: string; quote: string }[] }[];
  evidence: {
    passages: { passage: EvidencePassage; reason: string; score: number; lexical_rank?: number | null; vector_rank?: number | null; similarity?: number | null; embedding_profile?: string | null }[];
    coverage: string;
    warnings: string[];
    elapsed_ms: number;
    evidence_tokens: number;
    model_calls: number;
    embedding_calls: number;
    embedding_input_tokens: number | null;
    retrieval: "lexical" | "hybrid";
  };
  input_tokens: number | null;
  output_tokens: number | null;
  model: string | null;
  elapsed_ms: number;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function validEmbeddingReceipt(value: Record<string, unknown>): boolean {
  return typeof value.source_id === "string"
    && [value.prepared, value.remaining, value.embedding_calls].every((count) => finite(count) && Number.isInteger(count))
    && finite(value.elapsed_ms) && typeof value.active === "boolean"
    && ["not_queued", "queued", "running", "complete", "failed", "cancelled"].includes(String(value.status))
    && (value.error === null || typeof value.error === "string")
    && (value.input_tokens === null || (finite(value.input_tokens) && Number.isInteger(value.input_tokens)));
}

export function validEvidenceResponse(path: string, value: unknown): boolean {
  if (!record(value)) return false;
  if (path.endsWith("/index")) return typeof value.source_id === "string" && typeof value.project === "string" && finite(value.passages);
  if (path.endsWith("/remove-index")) return typeof value.removed === "boolean";
  if (path.endsWith("/embed") || path.endsWith("/embedding-status")) return validEmbeddingReceipt(value);
  const evidence = path.endsWith("/search") ? value : value.evidence;
  if (!record(evidence) || !strings(evidence.warnings) || typeof evidence.coverage !== "string"
    || !finite(evidence.elapsed_ms) || !finite(evidence.evidence_tokens) || !finite(evidence.model_calls)
    || !finite(evidence.embedding_calls) || !Number.isInteger(evidence.embedding_calls) || evidence.embedding_calls > 1
    || !["lexical", "hybrid"].includes(String(evidence.retrieval))
    || !(evidence.embedding_input_tokens === null || (finite(evidence.embedding_input_tokens) && Number.isInteger(evidence.embedding_input_tokens)))
    || !Array.isArray(evidence.passages) || evidence.passages.length > 6) return false;
  if (!evidence.passages.every((hit: unknown) => {
    if (!record(hit) || !record(hit.passage) || !finite(hit.score) || typeof hit.reason !== "string") return false;
    const p = hit.passage;
    return ["id", "source_id", "source_hash", "canonical_hash", "prefix", "content"].every((key) => typeof p[key] === "string")
      && finite(p.tokens) && p.tokens <= 650 && strings(p.warnings) && Array.isArray(p.spans) && p.spans.length > 0
      && p.spans.every((span: unknown) => record(span) && typeof span.block_id === "string" && finite(span.start)
        && finite(span.end) && span.end > span.start && ["characters", "rows"].includes(String(span.unit))
        && record(span.provenance) && ["page", "line_start", "line_end"].every((key) => span.provenance && record(span.provenance) && (span.provenance[key] === null || finite(span.provenance[key])))
        && ["sheet", "cell_range"].every((key) => record(span.provenance) && (span.provenance[key] === null || typeof span.provenance[key] === "string")));
  })) return false;
  if (path.endsWith("/search")) return true;
  const known = new Map(evidence.passages.map((hit) => [hit.passage.id, hit.passage.content]));
  if (!Array.isArray(value.claims) || value.claims.length > 6 || !value.claims.every((claim: unknown) => record(claim)
    && typeof claim.text === "string" && claim.text.length > 0 && Array.isArray(claim.citations) && claim.citations.length > 0
    && claim.citations.every((citation: unknown) => record(citation) && typeof citation.passage_id === "string"
      && typeof citation.quote === "string" && citation.quote.trim().length > 0 && known.get(citation.passage_id)?.includes(citation.quote)))) return false;
  if ((value.status === "answered") !== (value.claims.length > 0)) return false;
  return typeof value.text === "string" && finite(value.elapsed_ms) && ["answered", "evidence_only", "insufficient_evidence", "tool_required", "generation_unavailable"].includes(String(value.status))
    && ["input_tokens", "output_tokens"].every((key) => value[key] === null || finite(value[key]))
    && (value.model === null || typeof value.model === "string");
}
