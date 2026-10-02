import type { KnowledgeSource } from "./types";

export const SOURCE_STATUS: Record<KnowledgeSource["status"], {
  label: string;
  tone: "success" | "warning" | "danger" | "muted";
}> = {
  uploaded: { label: "Uploaded", tone: "muted" },
  processing: { label: "Processing", tone: "warning" },
  chunked: { label: "Chunked", tone: "warning" },
  ready_for_embedding: { label: "Ready for embedding", tone: "success" },
  embedding: { label: "Embedding", tone: "warning" },
  indexed: { label: "Indexed", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

export function isSourcePending(source: KnowledgeSource): boolean {
  if (source.is_active === false) return false;
  return ["uploaded", "processing", "chunked", "embedding"].includes(source.status) || source.embedding_status === "pending";
}

/** Keep newer upload results while including records from an earlier list request. */
export function mergeSources(older: KnowledgeSource[], newer: KnowledgeSource[]): KnowledgeSource[] {
  const recentIds = new Set(newer.map((source) => source.id));
  return [...newer, ...older.filter((source) => !recentIds.has(source.id))];
}
