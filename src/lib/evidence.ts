import { apiFetch } from "./api";
import type { EvidenceAnswer } from "./evidence-contracts";
export type { EvidenceAnswer } from "./evidence-contracts";
import type { EmbeddingReceipt } from "./evidence-contracts";

export function prepareEvidence(sourceId: string, signal?: AbortSignal) {
  return apiFetch<EmbeddingReceipt>(`/api/evidence/sources/${encodeURIComponent(sourceId)}/embed`, { method: "POST", signal });
}

export function embeddingStatus(sourceId: string, signal?: AbortSignal) {
  return apiFetch<EmbeddingReceipt>(`/api/evidence/sources/${encodeURIComponent(sourceId)}/embedding-status`, { signal });
}

export function indexEvidence(sourceId: string, project: string, title: string, documentDate: string, signal?: AbortSignal) {
  return apiFetch<{ source_id: string; project: string; passages: number }>(`/api/evidence/sources/${encodeURIComponent(sourceId)}/index`, {
    method: "POST", signal, body: JSON.stringify({ project, title: title || null, document_date: documentDate || null }),
  });
}

export function askEvidence(question: string, project: string, synthesize: boolean, signal?: AbortSignal, retrieval: "lexical" | "hybrid" = "lexical") {
  return apiFetch<EvidenceAnswer>("/api/evidence/answer", {
    method: "POST", signal, body: JSON.stringify({ question, project, synthesize, retrieval }),
  });
}
