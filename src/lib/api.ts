import { validResponse } from "./response-validation";
import { requestTimeoutMs } from "./request-timeout";
import type {
  ChatMessage, DebugRequest, DebugSession, Diagnosis, KnowledgeSource, SavedSolution, SourceReference, SourceType,
} from "./types";

export type { DebugRequest } from "./types";

const API_URL = "/api/backend";

export interface BackendHealth {
  status: string;
  service: string;
  api: string;
  database: string;
  pgvector: string;
  schema: string;
  revision: string | null;
  expected_revision: string | null;
  sources: number | null;
  documents: number | null;
  chunks: number | null;
  embedded_chunks: number | null;
  pending_sources: number | null;
  failed_sources: number | null;
  embedding_configured: boolean;
  ai_generation: string;
  answer_service?: "ready" | "unavailable" | "not_configured";
  retrieval_mode?: "keyword" | "dense";
}

export function checkBackendHealth(signal?: AbortSignal): Promise<BackendHealth> {
  return apiFetch("/health", { signal }, [503]);
}

export async function apiFetch<T>(path: string, init?: RequestInit, acceptedStatuses: number[] = []): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    const timeout = AbortSignal.timeout(requestTimeoutMs(path, init?.method));
    const signal = init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
    response = await fetch(`${API_URL.replace(/\/$/, "")}${path}`, { ...init, headers, signal });
  } catch (error) {
    if (init?.signal?.aborted) throw error;
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new Error("The backend took too long to respond. Please try again.");
    }
    throw new Error("Could not connect to the FixFlow backend.", { cause: error });
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok && !acceptedStatuses.includes(response.status)) throw new Error(apiErrorMessage(body, response.status));
  if (!validResponse(path, body, init?.method)) throw new Error("The backend returned an invalid response. Please try again.");
  return body as T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function apiErrorMessage(body: unknown, status: number): string {
  if (isRecord(body)) {
    if (typeof body.detail === "string") return body.detail;
    if (isRecord(body.error) && typeof body.error.message === "string") return body.error.message;
    if (typeof body.error === "string") return body.error;
  }
  return `API request failed (${status})`;
}

export function diagnose(req: DebugRequest, signal?: AbortSignal): Promise<Diagnosis> {
  return apiFetch(req.question ? "/api/ask" : "/api/debug", {
    method: "POST", signal,
    body: JSON.stringify({
      question: req.question, error: req.error, code: req.code, context: req.context,
      repo_url: req.repoUrl, techs: req.techs, files: req.files,
    }),
  });
}

export function sendFollowUp(question: string, sessionId: string, signal?: AbortSignal): Promise<ChatMessage> {
  return apiFetch("/api/chat", { method: "POST", signal, body: JSON.stringify({ question, session_id: sessionId }) });
}

export function listSessions(signal?: AbortSignal): Promise<DebugSession[]> {
  return apiFetch("/api/sessions", { signal });
}

export function getSession(id: string, signal?: AbortSignal): Promise<Diagnosis> {
  return apiFetch(`/api/sessions/${encodeURIComponent(id)}`, { signal });
}

export function listMessages(id: string, signal?: AbortSignal): Promise<ChatMessage[]> {
  return apiFetch(`/api/sessions/${encodeURIComponent(id)}/messages`, { signal });
}

export function listKnowledgeSources(signal?: AbortSignal): Promise<KnowledgeSource[]> {
  return apiFetch("/api/sources", { signal });
}

export function getSourceStatus(id: string, signal?: AbortSignal): Promise<KnowledgeSource> {
  return apiFetch(`/api/sources/${encodeURIComponent(id)}/status`, { signal });
}

export function addKnowledgeSource(input: {
  kind: "docs" | "github" | "upload";
  ingestionFormat?: "document" | "okf";
  value: string;
  content?: string;
  file?: File;
}, signal?: AbortSignal): Promise<KnowledgeSource> {
  const form = new FormData();
  form.set("kind", input.kind);
  form.set("ingestion_format", input.ingestionFormat ?? "document");
  form.set("value", input.value);
  if (input.content) form.set("content", input.content);
  if (input.file) form.set("file", input.file);
  return apiFetch("/api/documents", { method: "POST", body: form, signal });
}

export function listSaved(signal?: AbortSignal): Promise<SavedSolution[]> {
  return apiFetch("/api/saved", { signal });
}

export function saveSolution(input: {
  problem: string;
  rootCause: string;
  technology: string[];
  fixSummary: string;
  sources: SourceReference[];
}): Promise<SavedSolution> {
  return apiFetch("/api/saved", { method: "POST", body: JSON.stringify(input) });
}

export const SOURCE_TYPE_LABEL: Record<SourceType, string> = {
  docs: "Documentation", github: "GitHub Issue", community: "Community", code: "Code Example",
};

export function retryEmbedding(id: string): Promise<KnowledgeSource> {
  return apiFetch(`/api/sources/${encodeURIComponent(id)}/retry-embedding`, { method: "POST" });
}

export function setSourceAvailability(id: string, active: boolean): Promise<KnowledgeSource> {
  return apiFetch(`/api/sources/${encodeURIComponent(id)}/availability`, {
    method: "POST", body: JSON.stringify({ active }),
  });
}
