import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentPanel } from "@/components/workspace/agent-panel";
import { ChatAnswer } from "@/components/workspace/chat-answer";
import { validWorkflowResponse } from "@/lib/agent-contracts";
import type { ChatMessage } from "@/lib/chat";

const calls = vi.hoisted(() => ({ sendChatMessage: vi.fn(), chatMessage: vi.fn(), chatThread: vi.fn(), stopChatMessage: vi.fn() }));
const apps = vi.hoisted(() => ({ listConnectors: vi.fn() }));
const actions = vi.hoisted(() => ({ executeWorkflowAction: vi.fn() }));
vi.mock("@/lib/chat", () => calls);
vi.mock("@/lib/connectors", () => apps);
vi.mock("@/lib/agent", () => actions);
beforeEach(() => {
  Object.values(calls).forEach((call) => call.mockReset());
  actions.executeWorkflowAction.mockReset(); apps.listConnectors.mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });
const id = "11111111-1111-4111-8111-111111111111";
const nextId = "22222222-2222-4222-8222-222222222222";
const materialId = "e-" + "a".repeat(32);
function message(status: ChatMessage["status"] = "completed"): ChatMessage {
  return { id, parent_run_id: null, question: "Explain the project status", status, steps: 4, usage: { model_calls: 4, input_tokens: 400, output_tokens: 120 }, error: null, created_at: "2026-10-17T10:00:00Z",
    answer: status === "completed" ? { answer: "Here is the observed status.", claims: [{ text: "Payment tests are failing.", citations: [{ evidence_id: materialId, quote: "Payment tests are failing." }] }], limitations: ["Selected sources only."] } : null,
    materials: [{ id: materialId, title: "Manager feedback", text: "Payment tests are failing.", reference: { source_id: id }, observed_at: "2026-10-17T10:00:00Z", warning: null }],
    actions: [], files: [{ id, filename: "SRS.pdf", mime_type: "application/pdf", download_path: `/api/agent/files/${id}/download` }] };
}
function submit(question = "Explain project status") {
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: question } });
  fireEvent.submit(screen.getByRole("form", { name: "Chat" }));
}

describe("automatic workspace chat", () => {
  it("sends only a question and displays cited sources and private files without workflow controls", async () => {
    calls.sendChatMessage.mockResolvedValue(message());
    render(<AgentPanel />); submit();
    await screen.findByText("Here is the observed status.");
    expect(calls.sendChatMessage).toHaveBeenCalledWith("Explain project status", null, expect.any(AbortSignal));
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/Run workflow/)).toBeNull();
    expect(screen.getByRole("link", { name: "SRS.pdf" }).getAttribute("href")).toBe(`/api/backend/api/agent/files/${id}/download`);
    expect(screen.getByRole("link", { name: "[1]" }).getAttribute("href")).toBe(`#source-${id}-${materialId}`);
    expect(screen.queryByText(/model step/)).toBeNull();
  });

  it("continues the conversation automatically and keeps both messages", async () => {
    calls.sendChatMessage.mockResolvedValueOnce(message()).mockResolvedValueOnce({ ...message(), id: nextId, parent_run_id: id, question: "What should I do next?" });
    render(<AgentPanel />); submit();
    await screen.findByText("Here is the observed status.");
    submit("What should I do next?");
    await waitFor(() => expect(calls.sendChatMessage).toHaveBeenLastCalledWith("What should I do next?", id, expect.any(AbortSignal)));
    expect(await screen.findAllByRole("article", { name: "Chat message" })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByRole("article")).toBeNull();
    submit("Start again");
    await waitFor(() => expect(calls.sendChatMessage).toHaveBeenLastCalledWith("Start again", null, expect.any(AbortSignal)));
  });

  it("restores a private conversation from its URL and follows it up", async () => {
    calls.chatThread.mockResolvedValue([message()]);
    calls.sendChatMessage.mockResolvedValue({ ...message(), id: nextId, parent_run_id: id });
    render(<AgentPanel initialMessageId={id} />);
    await screen.findByText("Here is the observed status."); submit("Explain more");
    await waitFor(() => expect(calls.sendChatMessage).toHaveBeenCalledWith("Explain more", id, expect.any(AbortSignal)));
  });

  it("retains a failed prompt and blocks double submission while a message is being accepted", async () => {
    calls.sendChatMessage.mockRejectedValueOnce(new Error("Model unavailable"));
    render(<AgentPanel />); submit();
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Model unavailable");
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty("value", "Explain project status");
    calls.sendChatMessage.mockImplementation(() => new Promise(() => {}));
    submit(); fireEvent.submit(screen.getByRole("form", { name: "Chat" }));
    expect(calls.sendChatMessage).toHaveBeenCalledTimes(2);
  });

  it("stops polling after three failures and resumes only on explicit refresh", async () => {
    vi.useFakeTimers();
    calls.sendChatMessage.mockResolvedValue(message("queued")); calls.chatMessage.mockRejectedValue(new Error("Status unavailable"));
    render(<AgentPanel />); submit();
    await act(async () => { await Promise.resolve(); });
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(calls.chatMessage).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("alert").textContent).toContain("Status unavailable");
    calls.chatMessage.mockResolvedValue(message());
    fireEvent.click(screen.getByRole("button", { name: "Refresh answer" }));
    await act(async () => { await Promise.resolve(); });
    expect(calls.chatMessage).toHaveBeenCalledTimes(4);
    expect(screen.getByText("Here is the observed status.")).toBeDefined();
  });

  it("allows cancellation and aborts client requests on unmount", async () => {
    calls.sendChatMessage.mockResolvedValue(message("queued")); calls.stopChatMessage.mockResolvedValue(message("cancelled"));
    const view = render(<AgentPanel />); submit();
    fireEvent.click(await screen.findByRole("button", { name: "Stop answer" }));
    await screen.findByText("Answer stopped.");
    const signal = calls.stopChatMessage.mock.calls[0][1] as AbortSignal;
    view.unmount(); expect(signal.aborted).toBe(true);
  });

  it("keeps historical email approvals bound to the exact stored action", async () => {
    const original = { id, account_id: id, status: "draft" as const, payload: { recipient: "manager@example.com", subject: "Update", body: "Payment tests are failing." }, payload_hash: "b".repeat(64), provider_message_id: null, error: null };
    actions.executeWorkflowAction.mockResolvedValue({ ...original, status: "sent", provider_message_id: "gmail-receipt" });
    render(<ChatAnswer message={{ ...message(), actions: [original] }} />);
    fireEvent.click(screen.getByText("Previously prepared email · draft"));
    fireEvent.click(screen.getByRole("button", { name: "Send this exact email" }));
    await screen.findByText("Gmail receipt: gmail-receipt");
    expect(actions.executeWorkflowAction).toHaveBeenCalledWith(original);
  });
});

describe("chat and historical receipt validation", () => {
  it("rejects fabricated quotes, malformed action hashes, lineage, and external download URLs", () => {
    expect(validWorkflowResponse("/api/chat/messages", [message()])).toBe(true);
    expect(validWorkflowResponse(`/api/chat/messages/${id}/thread`, [message()])).toBe(true);
    const invented = message(); invented.answer!.claims[0].citations[0].quote = "Fabricated";
    expect(validWorkflowResponse(`/api/chat/messages/${id}`, invented)).toBe(false);
    const outside = message(); outside.files[0].download_path = "https://attacker.example/private.pdf";
    expect(validWorkflowResponse(`/api/agent/runs/${id}`, outside)).toBe(false);
    expect(validWorkflowResponse(`/api/chat/messages/${id}`, { ...message(), parent_run_id: 123 })).toBe(false);
  });
});
