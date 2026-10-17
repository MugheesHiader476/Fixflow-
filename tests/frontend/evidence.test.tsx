import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EvidencePanel } from "@/components/workspace/evidence-panel";
import { validEvidenceResponse, type EvidenceAnswer } from "@/lib/evidence-contracts";

const calls = vi.hoisted(() => ({ listKnowledgeSources: vi.fn(), askEvidence: vi.fn(), indexEvidence: vi.fn(), prepareEvidence: vi.fn(), embeddingStatus: vi.fn() }));
vi.mock("@/lib/api", () => ({ listKnowledgeSources: calls.listKnowledgeSources }));
vi.mock("@/lib/evidence", () => ({ askEvidence: calls.askEvidence, indexEvidence: calls.indexEvidence, prepareEvidence: calls.prepareEvidence, embeddingStatus: calls.embeddingStatus }));
beforeEach(() => Object.values(calls).forEach((call) => call.mockReset()));
afterEach(cleanup);

function result(): EvidenceAnswer {
  return {
    status: "evidence_only", text: "Source evidence is available.", claims: [], model: null,
    input_tokens: null, output_tokens: null, elapsed_ms: 30,
    evidence: { coverage: "project_indexed_snapshots", warnings: ["Snapshot coverage only."], elapsed_ms: 20, evidence_tokens: 45, model_calls: 0, embedding_calls: 0, embedding_input_tokens: null, retrieval: "lexical",
      passages: [{ score: 1, reason: "match", passage: { id: "p-evidence", source_id: "source-1", source_hash: "a".repeat(64), canonical_hash: "b".repeat(64), ordinal: 0, kind: "prose", tokens: 45,
        prefix: "Project: Atlas\nSource: Weekly Report\nSection: Release blockers\nDate: 2026-10-16", content: "Payment testing is incomplete.", warnings: [],
        spans: [{ block_id: "block-1", start: 0, end: 30, unit: "characters", provenance: { page: 2, line_start: null, line_end: null, sheet: null, cell_range: null } }],
      } }],
    },
  };
}

describe("project evidence workflow", () => {
  it("keeps synthesis explicit and renders coverage and reconstructable source locations", async () => {
    calls.askEvidence.mockResolvedValue(result());
    render(<EvidencePanel />);
    fireEvent.change(screen.getByRole("textbox", { name: "Project" }), { target: { value: "Atlas" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Question" }), { target: { value: "Why is release delayed?" } });
    fireEvent.click(screen.getByRole("button", { name: "Search evidence" }));
    await screen.findByText("Snapshot coverage only.");
    expect(calls.askEvidence).toHaveBeenCalledWith("Why is release delayed?", "Atlas", false, expect.any(AbortSignal), "lexical");
    expect(screen.getByText(/block-1: characters 0–30 · page 2/)).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Project" }), { target: { value: "Other" } });
    expect(screen.queryByText("Snapshot coverage only.")).toBeNull();
  });

  it("indexes only a selected parsed source under an explicit project", async () => {
    calls.listKnowledgeSources.mockResolvedValue([{ id: "parsed", name: "Report", status: "parsed", access_available: true }, { id: "pending", name: "Pending", status: "processing" }]);
    calls.indexEvidence.mockResolvedValue({ project: "Atlas", passages: 3 });
    render(<EvidencePanel />);
    fireEvent.change(screen.getByRole("textbox", { name: "Project" }), { target: { value: "Atlas" } });
    fireEvent.click(screen.getByText("Add a parsed source to this project"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh parsed sources" }));
    await screen.findByRole("option", { name: "Report" });
    expect(screen.queryByRole("option", { name: "Pending" })).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Source" }), { target: { value: "parsed" } });
    fireEvent.click(screen.getByRole("button", { name: "Add source to project" }));
    await waitFor(() => expect(calls.indexEvidence).toHaveBeenCalledWith("parsed", "Atlas", "", "", expect.any(AbortSignal)));
    expect(await screen.findByText("3 passages added to Atlas.")).toBeTruthy();
  });

  it("shows failures without making repeated model calls and cancels requests on unmount", async () => {
    calls.askEvidence.mockRejectedValueOnce(new Error("Access changed; search again."));
    const view = render(<EvidencePanel />);
    fireEvent.change(screen.getByRole("textbox", { name: "Project" }), { target: { value: "Atlas" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Question" }), { target: { value: "Why?" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Search evidence" }));
    await screen.findByText("Access changed; search again.");
    expect(calls.askEvidence).toHaveBeenCalledTimes(1);
    calls.askEvidence.mockImplementation(() => new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: "Search evidence" }));
    const signal = calls.askEvidence.mock.calls[1][3] as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
  });

  it("prepares only an explicit background job and sends hybrid queries only when selected", async () => {
    calls.listKnowledgeSources.mockResolvedValue([{ id: "parsed", name: "Report", status: "parsed", access_available: true }]);
    calls.prepareEvidence.mockResolvedValue({ prepared: 16, remaining: 4, embedding_calls: 1, status: "queued", active: false, error: null });
    calls.askEvidence.mockResolvedValue(result());
    render(<EvidencePanel />);
    expect(calls.prepareEvidence).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "Project" }), { target: { value: "Atlas" } });
    fireEvent.click(screen.getByText("Add a parsed source to this project"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh parsed sources" }));
    await screen.findByRole("option", { name: "Report" });
    fireEvent.change(screen.getByRole("combobox", { name: "Source" }), { target: { value: "parsed" } });
    fireEvent.click(screen.getByRole("button", { name: "Prepare semantic search" }));
    await screen.findByText("queued: 16 passages prepared; 4 remaining. 1 embedding API call(s). Lexical search remains available.");
    expect(calls.prepareEvidence).toHaveBeenCalledTimes(1);
    calls.embeddingStatus.mockResolvedValue({ prepared: 20, remaining: 0, embedding_calls: 2, status: "complete", active: true, error: null });
    fireEvent.click(screen.getByRole("button", { name: "Refresh semantic status" }));
    await screen.findByText("complete: 20 passages prepared; 0 remaining. 2 embedding API call(s). Semantic search active.");
    expect(calls.embeddingStatus).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("combobox", { name: "Search mode" }), { target: { value: "hybrid" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Question" }), { target: { value: "Explain authentication" } });
    fireEvent.click(screen.getByRole("button", { name: "Search evidence" }));
    await waitFor(() => expect(calls.askEvidence).toHaveBeenCalledWith("Explain authentication", "Atlas", false, expect.any(AbortSignal), "hybrid"));
  });
});

describe("evidence receipt validation", () => {
  it("rejects missing spans, oversized passages and fabricated citation quotes", () => {
    const good = result();
    expect(validEvidenceResponse("/api/evidence/answer", good)).toBe(true);
    const missing = result(); missing.evidence.passages[0].passage.spans = [];
    expect(validEvidenceResponse("/api/evidence/answer", missing)).toBe(false);
    const large = result(); large.evidence.passages[0].passage.tokens = 651;
    expect(validEvidenceResponse("/api/evidence/answer", large)).toBe(false);
    const invented = result(); invented.status = "answered"; invented.claims = [{ text: "Claim", citations: [{ passage_id: "p-evidence", quote: "Invented evidence" }] }];
    expect(validEvidenceResponse("/api/evidence/answer", invented)).toBe(false);
    invented.claims[0].citations[0].quote = "Payment testing is incomplete.";
    expect(validEvidenceResponse("/api/evidence/answer", invented)).toBe(true);
  });

  it("rejects invalid embedding receipts and usage", () => {
    const receipt = { source_id: "source", prepared: 16, remaining: 4, embedding_calls: 1, status: "queued", active: false, error: null, input_tokens: 123, elapsed_ms: 20 };
    expect(validEvidenceResponse("/api/evidence/sources/source/embed", receipt)).toBe(true);
    expect(validEvidenceResponse("/api/evidence/sources/source/embed", { ...receipt, remaining: -1 })).toBe(false);
    expect(validEvidenceResponse("/api/evidence/sources/source/embed", { ...receipt, embedding_calls: -1 })).toBe(false);
    expect(validEvidenceResponse("/api/evidence/sources/source/embedding-status", { ...receipt, embedding_calls: 2 })).toBe(true);
    expect(validEvidenceResponse("/api/evidence/sources/source/embed", { ...receipt, status: "unknown" })).toBe(false);
    const bad = result(); bad.evidence.embedding_calls = 2;
    expect(validEvidenceResponse("/api/evidence/answer", bad)).toBe(false);
  });
});
