import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DebugInput } from "@/components/debug/debug-input";
import { DiagnosisResult } from "@/components/debug/diagnosis-result";
import { FollowUpChat } from "@/components/debug/followup-chat";
import { PipelineProgress } from "@/components/debug/pipeline";
import { RagTransparency } from "@/components/debug/rag-transparency";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CodeBlock } from "@/components/ui/code-block";
import { Tabs, Tooltip } from "@/components/ui/tabs";
import { ToastProvider, useToast } from "@/components/ui/toast";
import { ASYNCIO_DIAGNOSIS } from "./fixtures/diagnosis";

const sendFollowUp = vi.hoisted(() => vi.fn());
const listMessages = vi.hoisted(() => vi.fn().mockResolvedValue([]));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  sendFollowUp,
  listMessages,
}));

function withToasts(component: React.ReactNode) {
  return render(<ToastProvider>{component}</ToastProvider>);
}

function ToastTrigger() {
  const { toast } = useToast();
  return <button onClick={() => toast("Saved", "success")}>Notify</button>;
}

describe("shared UI", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders button, badge, tooltip, and tabs states", () => {
    const onChange = vi.fn();
    render(
      <>
        <Button loading>Save</Button>
        <Badge tone="success">Ready</Badge>
        <Tooltip label="Helpful text"><button>Help</button></Tooltip>
        <Tabs
          items={[{ id: "one", label: "One" }, { id: "two", label: "Two" }]}
          value="one"
          onChange={onChange}
        />
      </>
    );

    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true);
    expect(screen.getByText("Ready")).toBeDefined();
    expect(screen.getByRole("tooltip").textContent).toBe("Helpful text");
    fireEvent.click(screen.getByRole("tab", { name: "Two" }));
    expect(onChange).toHaveBeenCalledWith("two");
  });

  it("shows pipeline and expandable RAG details", () => {
    render(
      <>
        <PipelineProgress />
        <RagTransparency rag={ASYNCIO_DIAGNOSIS.rag} />
      </>
    );

    expect(screen.getByRole("status")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /How FixFlow found/ }));
    expect(screen.getByText("Keyword retrieval")).toBeDefined();
    expect(screen.getByText("24", { selector: "p" })).toBeDefined();
  });

  it("copies code and lets users dismiss notifications", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    withToasts(
      <>
        <CodeBlock code="print('ready')" language="python" label="Fix" />
        <ToastTrigger />
      </>
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("print('ready')"));
    fireEvent.click(screen.getByRole("button", { name: "Notify" }));
    expect(screen.getByText("Saved")).toBeDefined();
    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss notification" }).at(-1)!);
    expect(screen.queryByText("Saved")).toBeNull();
  });
});

describe("debug interactions", () => {
  afterEach(cleanup);

  it("validates input and submits the example error", () => {
    const onDiagnose = vi.fn();
    withToasts(
      <DebugInput
        onDiagnose={onDiagnose}
        busy={false}
        onFilesChange={vi.fn()}
        onTechsChange={vi.fn()}
        onRepoChange={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Ask question" }));
    expect(screen.getByText("Add a question, error message, code, or context first")).toBeDefined();
    fireEvent.click(screen.getByRole("tab", { name: "Error" }));
    fireEvent.click(screen.getByRole("button", { name: /try example/i }));
    fireEvent.click(screen.getByRole("button", { name: "Search documentation" }));
    expect(onDiagnose).toHaveBeenCalledWith(
      expect.objectContaining({ error: "RuntimeError: no running event loop" })
    );
  });

  it("exports and saves a rendered diagnosis", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const onSaved = vi.fn();
    withToasts(<DiagnosisResult diagnosis={ASYNCIO_DIAGNOSIS} onSaved={onSaved} />);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSaved).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("# FixFlow diagnosis")));
    expect(screen.queryByRole("button", { name: "Apply Patch" })).toBeNull();
  });

  it("shows the diagnosis status returned by the backend", () => {
    withToasts(
      <DiagnosisResult
        diagnosis={{ ...ASYNCIO_DIAGNOSIS, status: "investigating" }}
        onSaved={vi.fn()}
      />
    );

    expect(screen.getByText("Investigation in Progress")).toBeDefined();
    expect(screen.queryByText("Likely Cause Found")).toBeNull();
  });

  it("captures code, context, files, and selected technologies", async () => {
    const onDiagnose = vi.fn();
    const onFilesChange = vi.fn();
    const onTechsChange = vi.fn();
    const onRepoChange = vi.fn();
    const { container } = withToasts(
      <DebugInput
        onDiagnose={onDiagnose}
        busy={false}
        onFilesChange={onFilesChange}
        onTechsChange={onTechsChange}
        onRepoChange={onRepoChange}
      />
    );

    fireEvent.click(screen.getByRole("tab", { name: "Code" }));
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "print('ready')" } });
    expect(screen.getByText("Code preview (first 8,000 characters)")).toBeDefined();
    fireEvent.click(screen.getByRole("tab", { name: "Context" }));
    fireEvent.change(screen.getByLabelText("Problem context"), { target: { value: "Worker failed" } });
    fireEvent.change(screen.getByLabelText("GitHub repository URL"), {
      target: { value: "https://github.com/example/project" },
    });
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    fireEvent.change(fileInput!, { target: { files: [new File(["code"], "worker.py")] } });
    await waitFor(() => expect(onFilesChange).toHaveBeenCalledWith(["worker.py"]));
    fireEvent.click(screen.getByRole("button", { name: /Select technology/ }));
    fireEvent.click(screen.getByRole("button", { name: "Python" }));
    fireEvent.keyDown(screen.getByLabelText("Problem context"), { key: "Enter", ctrlKey: true });

    expect(onRepoChange).toHaveBeenCalledWith("https://github.com/example/project");
    expect(onFilesChange).toHaveBeenCalledWith(["worker.py"]);
    expect(onTechsChange).toHaveBeenCalledWith(["Python"]);
    expect(onDiagnose).toHaveBeenCalledWith(expect.objectContaining({
      context: "Worker failed", files: [{ name: "worker.py", content: "code" }],
    }));
  });

  it("sends a follow-up and renders its grounded source", async () => {
    sendFollowUp.mockResolvedValue({
      id: "reply-1",
      role: "fixflow",
      text: "Await the coroutine directly.",
      sources: [{ title: "Async docs", type: "docs" }],
    });
    withToasts(<FollowUpChat sessionId="session-1" confidence={88} />);

    await waitFor(() => expect(screen.getByLabelText("Follow-up question")).toHaveProperty("disabled", false));

    fireEvent.change(screen.getByLabelText("Follow-up question"), {
      target: { value: "How should I call it?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send follow-up" }));

    expect(await screen.findByText("Await the coroutine directly.")).toBeDefined();
    expect(screen.getByText("Async docs")).toBeDefined();
    expect(sendFollowUp).toHaveBeenCalledWith(
      "How should I call it?",
      "session-1",
      expect.any(AbortSignal)
    );
  });

  it("restores persisted conversation and resets it when switching sessions", async () => {
    listMessages.mockResolvedValueOnce([{ id: "old", role: "user", text: "Earlier question" }]);
    const view = withToasts(<FollowUpChat sessionId="first" confidence={null} />);
    expect(await screen.findByText("Earlier question")).toBeDefined();
    view.rerender(<ToastProvider><FollowUpChat sessionId="second" confidence={null} /></ToastProvider>);
    await waitFor(() => expect(screen.getByLabelText("Follow-up question")).toHaveProperty("disabled", false));
    expect(screen.queryByText("Earlier question")).toBeNull();
  });

  it("restores an unsent question after a chat failure", async () => {
    sendFollowUp.mockRejectedValueOnce(new Error("offline"));
    withToasts(<FollowUpChat sessionId="failed" confidence={null} />);
    const input = screen.getByLabelText("Follow-up question");
    await waitFor(() => expect(input).toHaveProperty("disabled", false));
    fireEvent.change(input, { target: { value: "Retry this question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send follow-up" }));
    await waitFor(() => expect(input).toHaveProperty("value", "Retry this question"));
    expect(screen.queryByText("Retry this question")).toBeNull();
  });

  it("renders an honest retrieval-only result without confidence or fabricated code", async () => {
    withToasts(<DiagnosisResult diagnosis={{
      ...ASYNCIO_DIAGNOSIS, generation: "disabled", confidence: null, codeFix: null, alternatives: [],
    }} onSaved={vi.fn()} />);
    expect(screen.getByText(/AI diagnosis and suggested code changes are not connected/)).toBeDefined();
    expect(screen.queryByText("92%")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy code" })).toBeNull();
    await waitFor(() => expect(screen.getByLabelText("Follow-up question")).toHaveProperty("disabled", false));
  });
});
