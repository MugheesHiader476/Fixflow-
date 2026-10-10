/** Validate the response fields that drive rendering before trusting backend JSON. */
export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): value is string { return typeof value === "string"; }
function count(value: unknown): boolean { return typeof value === "number" && Number.isInteger(value) && value >= 0; }
function strings(value: unknown): boolean { return Array.isArray(value) && value.every(text); }
function confidence(value: unknown): boolean { return value === null || (count(value) && Number(value) <= 100); }
function list(value: unknown, check: (item: unknown) => boolean): boolean { return Array.isArray(value) && value.every(check); }
function fields(value: Record<string, unknown>, names: string[]): boolean { return names.every((name) => text(value[name])); }
function reference(value: unknown): boolean {
  return record(value) && text(value.title) && ["docs", "github", "community", "code"].includes(String(value.type))
    && ["id", "source_id", "url", "excerpt", "location", "quote"].every((name) => value[name] === undefined || value[name] === null || text(value[name]))
    && (value.number === undefined || value.number === null || (count(value.number) && Number(value.number) > 0));
}

function validAnswer(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (!record(value) || !text(value.text) || !["answered", "insufficient_evidence"].includes(String(value.status))) return false;
  if (!(value.model === null || text(value.model))) return false;
  if (!list(value.citations, (citation) => reference(citation) && record(citation) && text(citation.id)
    && text(citation.quote) && count(citation.number) && Number(citation.number) > 0)) return false;
  const citations = value.citations as Record<string, unknown>[];
  if (!citations.every((citation, index) => citation.number === index + 1 && citation.quote !== ""
    && (citation.excerpt === undefined || citation.excerpt === null || String(citation.excerpt).includes(String(citation.quote))))) return false;
  return value.status === "answered" ? citations.length > 0 : citations.length === 0;
}

function validRequest(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  return record(value) && strings(value.techs)
    && ["question", "error", "code", "context", "repo_url"].every((name) => value[name] === undefined || value[name] === null || text(value[name]))
    && (value.files === undefined || list(value.files, (item) => record(item) && fields(item, ["name", "content"])));
}

function validHealth(value: unknown): boolean {
  return record(value) && ["ok", "degraded"].includes(String(value.status))
    && fields(value, ["service", "api", "database", "pgvector", "schema", "ai_generation"])
    && ["revision", "expected_revision"].every((name) => value[name] === null || text(value[name]))
    && ["sources", "documents", "chunks", "embedded_chunks", "pending_sources", "failed_sources"].every((name) => value[name] === null || count(value[name]))
    && typeof value.embedding_configured === "boolean"
    && (value.answer_service === undefined || ["ready", "unavailable", "not_configured"].includes(String(value.answer_service)));
}

export function validSource(value: unknown): boolean {
  if (!record(value) || !fields(value, ["id", "source_id", "name", "created_at", "updated", "detail"])) return false;
  return (value.is_active === undefined || typeof value.is_active === "boolean")
    && (value.managed_by_connector === undefined || typeof value.managed_by_connector === "boolean")
    && (value.retrieval_available === undefined || typeof value.retrieval_available === "boolean")
    && ["docs", "github", "community", "upload"].includes(String(value.kind))
    && ["docs", "github", "community", "upload"].includes(String(value.source_type))
    && ["uploaded", "processing", "chunked", "ready_for_embedding", "embedding", "indexed", "failed"].includes(String(value.status))
    && ["documents", "document_count", "chunks", "chunk_count"].every((name) => count(value[name]))
    && (value.error_message === null || text(value.error_message))
    && (value.embedding_status === undefined || ["not_configured", "pending", "processing", "complete", "failed"].includes(String(value.embedding_status)))
    && (value.ingestion_format === undefined || ["document", "okf"].includes(String(value.ingestion_format)))
    && (value.embedding_error === undefined || value.embedding_error === null || text(value.embedding_error));
}

function validDiagnosis(value: unknown): boolean {
  if (!record(value) || !fields(value, ["sessionId", "rootCause", "whyThisHappens"]) || !record(value.rag)) return false;
  const rag = value.rag;
  return ["likely-cause-found", "investigating", "no-cause"].includes(String(value.status))
    && confidence(value.confidence) && strings(value.detected)
    && list(value.recommendedFix, (item) => record(item) && fields(item, ["title", "detail"]))
    && list(value.alternatives, (item) => record(item) && fields(item, ["title", "tradeoff", "summary"]))
    && list(value.sources, (item) => reference(item) && record(item) && fields(item, ["id", "publisher", "url", "excerpt"]) && count(item.relevance) && Number(item.relevance) <= 100 && typeof item.used === "boolean")
    && text(rag.query) && strings(rag.expansions)
    && ["retrieved", "reranked", "sourcesUsed"].every((name) => count(rag[name]))
    && (rag.retrievalMethod === undefined || ["keyword", "dense"].includes(String(rag.retrievalMethod)))
    && list(rag.topChunks, (item) => record(item) && text(item.doc) && typeof item.score === "number" && Number.isFinite(item.score))
    && (value.codeFix === null || (record(value.codeFix) && fields(value.codeFix, ["file", "lines", "before", "after"]) && ["python", "typescript", "javascript", "bash", "sql"].includes(String(value.codeFix.language))))
    && validRequest(value.request)
    && validAnswer(value.answer)
    && (value.generation === undefined || ["disabled", "model", "legacy"].includes(String(value.generation)));
}

function validMessage(value: unknown): boolean {
  return record(value) && fields(value, ["id", "text"]) && ["user", "fixflow"].includes(String(value.role))
    && validAnswer(value.answer)
    && (value.sources === undefined || list(value.sources, reference));
}
function validSaved(value: unknown): boolean {
  return record(value) && fields(value, ["id", "problem", "rootCause", "fixSummary", "savedAt"])
    && strings(value.technology) && list(value.sources, reference);
}
function validSession(value: unknown): boolean {
  return record(value) && fields(value, ["id", "title", "createdAt", "errorMessage"])
    && strings(value.technology) && confidence(value.confidence)
    && ["resolved", "unresolved", "in-progress"].includes(String(value.status));
}

export function validResponse(path: string, value: unknown, method = "GET"): boolean {
  if (path.startsWith("/api/connectors")) return validConnectorResponse(path, value);
  if (path === "/health" || path === "/api/readiness") return validHealth(value);
  if (path === "/api/sources") return list(value, validSource);
  if (path.startsWith("/api/sources/")) return validSource(value);
  if (path === "/api/documents") return validSource(value);
  if (path === "/api/sessions") return list(value, validSession);
  if (path.endsWith("/messages")) return list(value, validMessage);
  if (path === "/api/chat") return validMessage(value);
  if (path === "/api/saved") return method === "POST" ? validSaved(value) : list(value, validSaved);
  return validDiagnosis(value);
}
import { validConnectorResponse } from "./connector-contracts";
