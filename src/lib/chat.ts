import { apiFetch } from "./api";
import type { WorkflowRun } from "./agent";

export type ChatMessage = WorkflowRun;

export function sendChatMessage(question: string, parentMessageId: string | null, signal?: AbortSignal): Promise<ChatMessage> {
  return apiFetch("/api/chat/messages", {
    method: "POST", body: JSON.stringify({ question, parent_message_id: parentMessageId }), signal,
  });
}
export function chatMessage(id: string, signal?: AbortSignal): Promise<ChatMessage> {
  return apiFetch(`/api/chat/messages/${encodeURIComponent(id)}`, { signal });
}
export function chatHistory(signal?: AbortSignal): Promise<ChatMessage[]> {
  return apiFetch("/api/chat/messages", { signal });
}
export function chatThread(id: string, signal?: AbortSignal): Promise<ChatMessage[]> {
  return apiFetch(`/api/chat/messages/${encodeURIComponent(id)}/thread`, { signal });
}
export function stopChatMessage(id: string, signal?: AbortSignal): Promise<ChatMessage> {
  return apiFetch(`/api/chat/messages/${encodeURIComponent(id)}/cancel`, { method: "POST", signal });
}
