export type SourceType = "docs" | "github" | "community" | "code";

export interface DebugAttachment {
  name: string;
  content: string;
}

export interface DebugRequest {
  error?: string;
  code?: string;
  context?: string;
  repoUrl?: string;
  techs: string[];
  files?: DebugAttachment[];
}

export interface SourceDoc {
  id: string;
  type: SourceType;
  title: string;
  publisher: string;
  url: string;
  relevance: number;
  excerpt: string;
  used: boolean;
}

export interface FixStep {
  title: string;
  detail: string;
}

export interface CodeFix {
  file: string;
  lines: string;
  before: string;
  after: string;
  language: "python" | "typescript" | "javascript" | "bash" | "sql";
}

export interface AlternativeFix {
  title: string;
  tradeoff: string;
  summary: string;
}

export interface Diagnosis {
  sessionId: string;
  status: "likely-cause-found" | "investigating" | "no-cause";
  confidence: number | null;
  detected: string[];
  rootCause: string;
  whyThisHappens: string;
  recommendedFix: FixStep[];
  codeFix: CodeFix | null;
  alternatives: AlternativeFix[];
  sources: SourceDoc[];
  generation?: "disabled" | "model" | "legacy";
  request?: {
    error?: string | null;
    code?: string | null;
    context?: string | null;
    repo_url?: string | null;
    techs: string[];
    files?: DebugAttachment[];
  } | null;
  rag: {
    query: string;
    expansions: string[];
    retrieved: number;
    reranked: number;
    sourcesUsed: number;
    topChunks: { doc: string; score: number }[];
  };
}

export interface ChatMessage {
  id: string;
  role: "user" | "fixflow";
  text: string;
  sources?: { title: string; type: SourceType }[];
  pending?: boolean;
}

export type SessionStatus = "resolved" | "unresolved" | "in-progress";

export interface DebugSession {
  id: string;
  title: string;
  technology: string[];
  createdAt: string;
  status: SessionStatus;
  confidence: number | null;
  errorMessage: string;
}

export interface KnowledgeSource {
  is_active?: boolean;
  retrieval_available?: boolean;
  id: string;
  source_id: string;
  name: string;
  kind: "docs" | "github" | "community" | "upload";
  source_type: "docs" | "github" | "community" | "upload";
  status: "uploaded" | "processing" | "chunked" | "ready_for_embedding" | "embedding" | "indexed" | "failed";
  chunks: number;
  documents: number;
  document_count: number;
  chunk_count: number;
  ingestion_format?: "document" | "okf";
  embedding_status?: "not_configured" | "pending" | "processing" | "complete" | "failed";
  embedding_error?: string | null;
  error_message: string | null;
  created_at: string;
  updated: string;
  detail: string;
}

export interface SavedSolution {
  id: string;
  problem: string;
  rootCause: string;
  technology: string[];
  fixSummary: string;
  sources: { title: string; type: SourceType }[];
  savedAt: string;
}

export const TECH_OPTIONS = [
  "Python",
  "FastAPI",
  "Django",
  "JavaScript",
  "TypeScript",
  "React",
  "Next.js",
  "Node.js",
  "Docker",
  "PostgreSQL",
  "Git",
  "LangChain",
] as const;

export type TechOption = (typeof TECH_OPTIONS)[number];
