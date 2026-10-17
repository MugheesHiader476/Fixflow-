import { apiFetch } from "./api";

export interface WorkflowMaterial {
  id: string;
  title: string;
  text: string;
  reference: Record<string, unknown>;
  observed_at: string;
  warning: string | null;
}
export interface WorkflowAction {
  id: string;
  account_id: string;
  status: "draft" | "dispatching" | "sent" | "blocked" | "unknown";
  payload: { recipient?: string; subject?: string; body?: string };
  payload_hash: string;
  provider_message_id: string | null;
  error: string | null;
}
export interface WorkflowRun {
  id: string;
  question: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  answer: { answer: string; claims: { text: string; citations: { evidence_id: string; quote: string }[] }[]; limitations: string[] } | null;
  materials: WorkflowMaterial[];
  actions: WorkflowAction[];
  files: { id: string; filename: string; mime_type: string; download_path: string }[];
  steps: number;
  usage: Record<string, number | null>;
  error: string | null;
  created_at: string;
}
export interface WorkflowRequest {
  question: string;
  project: string | null;
  include_connections: boolean;
  action_mode: "draft" | "auto";
  allow_email_actions: boolean;
  allowed_recipients: string[];
  parent_run_id: string | null;
}
export function startWorkflow(request: WorkflowRequest, signal?: AbortSignal): Promise<WorkflowRun> {
  return apiFetch("/api/agent/runs", { method: "POST", body: JSON.stringify(request), signal });
}
export function workflowStatus(id: string, signal?: AbortSignal): Promise<WorkflowRun> {
  return apiFetch(`/api/agent/runs/${encodeURIComponent(id)}`, { signal });
}
export function workflowHistory(signal?: AbortSignal): Promise<WorkflowRun[]> {
  return apiFetch("/api/agent/runs", { signal });
}
export function cancelWorkflow(id: string): Promise<WorkflowRun> {
  return apiFetch(`/api/agent/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}
export function executeWorkflowAction(action: WorkflowAction): Promise<WorkflowAction> {
  return apiFetch(`/api/agent/actions/${encodeURIComponent(action.id)}/execute`, {
    method: "POST", body: JSON.stringify({ expected_hash: action.payload_hash }),
  });
}
