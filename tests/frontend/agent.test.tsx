import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentPanel } from "@/components/workspace/agent-panel";
import { validWorkflowResponse } from "@/lib/agent-contracts";
import type { WorkflowRun } from "@/lib/agent";

const calls = vi.hoisted(() => ({ startWorkflow: vi.fn(), workflowStatus: vi.fn(), workflowHistory: vi.fn(), cancelWorkflow: vi.fn(), executeWorkflowAction: vi.fn() }));
vi.mock("@/lib/agent", () => calls);
beforeEach(() => Object.values(calls).forEach((call) => call.mockReset()));
afterEach(() => { cleanup(); vi.useRealTimers(); });
const id = "11111111-1111-4111-8111-111111111111";
const materialId = "e-" + "a".repeat(32);
function run(status: WorkflowRun["status"] = "completed"): WorkflowRun {
  return { id, question: "Explain the project status", status, steps: 4, usage: { model_calls: 4, input_tokens: 400, output_tokens: 120 }, error: null, created_at: "2026-10-17T10:00:00Z",
    answer: status === "completed" ? { answer: "Here is the observed status.", claims: [{ text: "Payment tests are failing.", citations: [{ evidence_id: materialId, quote: "Payment tests are failing." }] }], limitations: ["A selected snapshot, not a complete live scan."] } : null,
    materials: [{ id: materialId, title: "Manager feedback", text: "Payment tests are failing.", reference: { source_id: id }, observed_at: "2026-10-17T10:00:00Z", warning: null }],
    actions: [{ id, account_id: id, status: "draft", payload: { recipient: "manager@example.com", subject: "Project update", body: "Payment tests are failing." }, payload_hash: "b".repeat(64), provider_message_id: null, error: null }],
    files: [{ id, filename: "SRS.pdf", mime_type: "application/pdf", download_path: `/api/agent/files/${id}/download` }] };
}
function submit() {
  fireEvent.change(screen.getByRole("textbox", { name: "Task" }), { target: { value: "Explain project status and draft a reply" } });
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
}

describe("workspace workflows", () => {
  it("defaults to drafts, displays grounded sources/files and sends only the exact reviewed email", async () => {
    calls.startWorkflow.mockResolvedValue(run());
    calls.executeWorkflowAction.mockResolvedValue({ ...run().actions[0], status: "sent", provider_message_id: "gmail-receipt" });
    render(<AgentPanel />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Allow email drafts and replies for this request" }));
    submit();
    await screen.findByText("Here is the observed status.");
    expect(calls.startWorkflow).toHaveBeenCalledWith(expect.objectContaining({ action_mode: "draft", allow_email_actions: true, allowed_recipients: [], include_connections: true }), expect.any(AbortSignal));
    expect(calls.executeWorkflowAction).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "SRS.pdf" }).getAttribute("href")).toBe( `/api/backend/api/agent/files/${id}/download`);
    fireEvent.click(screen.getByRole("button", { name: "Send this exact email" }));
    await screen.findByText("Gmail receipt: gmail-receipt");
    expect(calls.executeWorkflowAction).toHaveBeenCalledWith(run().actions[0]);
  });

  it("passes explicit recipient authorization and supports a manual-sources-only scope", async () => {
    calls.startWorkflow.mockResolvedValue(run());
    render(<AgentPanel />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Authorize automatic/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Use my currently authorized/ }));
    fireEvent.change(screen.getByRole("textbox", { name: /Authorized automatic recipients/ }), { target: { value: "manager@example.com" } });
    submit();
    await waitFor(() => expect(calls.startWorkflow).toHaveBeenCalledWith(expect.objectContaining({ action_mode: "auto", allow_email_actions: true, allowed_recipients: ["manager@example.com"], include_connections: false }), expect.any(AbortSignal)));
  });

  it("keeps email tools disabled for an ordinary question", async () => {
    calls.startWorkflow.mockResolvedValue({ ...run(), actions: [] });
    render(<AgentPanel />); submit();
    await screen.findByText("Here is the observed status.");
    expect(calls.startWorkflow).toHaveBeenCalledWith(expect.objectContaining({ allow_email_actions: false, action_mode: "draft" }), expect.any(AbortSignal));
    expect(calls.executeWorkflowAction).not.toHaveBeenCalled();
  });

  it("restores history and binds follow-up requests to the selected conversation", async () => {
    calls.workflowHistory.mockResolvedValue([run()]); calls.startWorkflow.mockResolvedValue(run());
    render(<AgentPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Load workflow history" }));
    const select = await screen.findByRole("combobox", { name: "Previous workflow" });
    fireEvent.change(select, { target: { value: id } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Continue this conversation" }));
    submit();
    await waitFor(() => expect(calls.startWorkflow).toHaveBeenCalledWith(expect.objectContaining({ parent_run_id: id }), expect.any(AbortSignal)));
  });

  it("stops automatic polling after three failures and resumes only on explicit refresh", async () => {
    vi.useFakeTimers();
    calls.startWorkflow.mockResolvedValue(run("queued")); calls.workflowStatus.mockRejectedValue(new Error("Status unavailable"));
    render(<AgentPanel />); submit();
    await act(async () => { await Promise.resolve(); });
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(calls.workflowStatus).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("alert").textContent).toContain("Status unavailable");
    calls.workflowStatus.mockResolvedValue(run());
    fireEvent.click(screen.getByRole("button", { name: "Refresh workflow" }));
    await act(async () => { await Promise.resolve(); });
    expect(calls.workflowStatus).toHaveBeenCalledTimes(4);
  });
});

describe("workflow receipt validation", () => {
  it("rejects fabricated quotations, malformed action hashes and external download URLs", () => {
    expect(validWorkflowResponse(`/api/agent/runs/${id}`, run())).toBe(true);
    const invented = run(); invented.answer!.claims[0].citations[0].quote = "Fabricated";
    expect(validWorkflowResponse(`/api/agent/runs/${id}`, invented)).toBe(false);
    const outside = run(); outside.files[0].download_path = "https://attacker.example/private.pdf";
    expect(validWorkflowResponse(`/api/agent/runs/${id}`, outside)).toBe(false);
    const invalid = run(); invalid.actions[0].payload_hash = "not-a-hash";
    expect(validWorkflowResponse(`/api/agent/runs/${id}`, invalid)).toBe(false);
  });
});
