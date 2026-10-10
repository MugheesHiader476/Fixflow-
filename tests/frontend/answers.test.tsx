import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DebugInput } from "@/components/debug/debug-input";
import { DiagnosisResult } from "@/components/debug/diagnosis-result";
import { ToastProvider } from "@/components/ui/toast";
import { diagnosisMarkdown } from "@/lib/diagnosis";
import { validResponse } from "@/lib/response-validation";
import type { Diagnosis, GroundedAnswer } from "@/lib/types";
import { ASYNCIO_DIAGNOSIS } from "./fixtures/diagnosis";

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()), listMessages: vi.fn().mockResolvedValue([]),
}));

const answer: GroundedAnswer = {
  status: "answered", model: "local-model:4b", text: "Refunds are available for 30 days. [1]", citations: [{
    number: 1, id: "chunk", title: "Policy", type: "docs", quote: "Refunds are available for 30 days.",
    excerpt: "Refunds are available for 30 days.", location: "Page 2", url: "https://example.test/policy",
  }],
};
const result: Diagnosis = {
  ...ASYNCIO_DIAGNOSIS, generation: "model", answer, confidence: null, detected: [], codeFix: null,
  recommendedFix: [], alternatives: [],
};
afterEach(cleanup);

describe("question answering", () => {
  it("opens with a general question and submits it without selecting technologies", () => {
    const onDiagnose = vi.fn();
    render(<ToastProvider><DebugInput busy={false} onDiagnose={onDiagnose} onFilesChange={vi.fn()} onTechsChange={vi.fn()} onRepoChange={vi.fn()} /></ToastProvider>);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "What is our refund policy?" } });
    fireEvent.keyDown(screen.getByLabelText("Your question"), { key: "Enter", ctrlKey: true });
    expect(onDiagnose).toHaveBeenCalledWith(expect.objectContaining({ question: "What is our refund policy?", techs: [] }));
  });

  it("shows inspectable citations and exports the same quotes", async () => {
    render(<ToastProvider><DiagnosisResult diagnosis={result} onSaved={vi.fn()} /></ToastProvider>);
    expect(screen.getByText(answer.text)).toBeDefined();
    fireEvent.click(screen.getByText("[1] Policy"));
    expect(screen.getByText(answer.citations[0].quote)).toBeDefined();
    expect(screen.getByText("Page 2")).toBeDefined();
    expect(screen.getByRole("link", { name: "Open original source" }).getAttribute("href")).toBe("https://example.test/policy");
    expect(screen.queryByText("Root Cause")).toBeNull();
    expect(screen.queryByText("0%")).toBeNull();
    expect(diagnosisMarkdown(result)).toContain("> Refunds are available for 30 days.");
    await waitFor(() => expect(screen.getByLabelText("Follow-up question")).toHaveProperty("disabled", false));
  });

  it("rejects missing citations, fake quote text, and invalid numbering at the response boundary", () => {
    expect(validResponse("/api/ask", result)).toBe(true);
    expect(validResponse("/api/ask", { ...result, answer: { ...answer, citations: [] } })).toBe(false);
    expect(validResponse("/api/ask", { ...result, answer: { ...answer, citations: [{ ...answer.citations[0], quote: "Invented policy" }] } })).toBe(false);
    expect(validResponse("/api/ask", { ...result, answer: { ...answer, citations: [{ ...answer.citations[0], number: 99 }] } })).toBe(false);
  });
});
