import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HistoryPage from "@/app/history/page";
import DebugSessionPage from "@/app/page";
import SavedPage from "@/app/saved/page";
import SettingsPage from "@/app/settings/page";
import SourcesPage from "@/app/sources/page";
import { ASYNCIO_DIAGNOSIS } from "./fixtures/diagnosis";

const api = vi.hoisted(() => ({
  addKnowledgeSource: vi.fn(),
  checkBackendHealth: vi.fn(),
  diagnose: vi.fn(),
  getSession: vi.fn(),
  listKnowledgeSources: vi.fn(),
  listSaved: vi.fn(),
  listSessions: vi.fn(),
  saveSolution: vi.fn(),
}));
const toast = vi.hoisted(() => vi.fn());
const navigation = vi.hoisted(() => ({ session: null as string | null, router: { replace: vi.fn() } }));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));
vi.mock("@/components/layout/theme-provider", () => ({
  useTheme: () => ({ dark: true, toggle: vi.fn() }),
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("@/components/layout/right-panel", () => ({ RightPanel: () => null }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: () => navigation.session }),
  useRouter: () => navigation.router,
}));
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@clerk/nextjs", () => ({
  Show: ({ children }: { children: ReactNode }) => <>{children}</>,
  SignInButton: ({ children }: { children: ReactNode }) => <>{children}</>,
  SignUpButton: ({ children }: { children: ReactNode }) => <>{children}</>,
  UserButton: () => <button>User</button>,
}));
vi.mock("@/components/debug/debug-input", () => ({
  DebugInput: ({ onDiagnose }: { onDiagnose: (request: { error: string; techs: string[] }) => void }) => (
    <button onClick={() => onDiagnose({ error: "event loop", techs: ["Python"] })}>
      Run diagnosis
    </button>
  ),
}));
vi.mock("@/components/debug/pipeline", () => ({ PipelineProgress: () => <p>Analyzing</p> }));
vi.mock("@/components/debug/diagnosis-result", () => ({
  DiagnosisResult: ({
    diagnosis,
    onSaved,
  }: {
    diagnosis: { rootCause: string };
    onSaved: () => void;
  }) => (
    <div>
      <p>{diagnosis.rootCause}</p>
      <button onClick={onSaved}>Save diagnosis</button>
    </div>
  ),
}));

beforeEach(() => {
  navigation.session = null;
  navigation.router.replace.mockReset();
  api.addKnowledgeSource.mockReset();
  api.checkBackendHealth.mockReset();
  api.diagnose.mockReset();
  api.getSession.mockReset();
  api.listKnowledgeSources.mockReset();
  api.listSaved.mockReset();
  api.listSessions.mockReset();
  api.saveSolution.mockReset();
  toast.mockReset();
});

afterEach(cleanup);

describe("application pages", () => {
  it("connects the editorial hero to the functional workspace and knowledge pages", () => {
    render(<DebugSessionPage />);
    expect(screen.getByRole("heading", { level: 1, name: /Less searching/ })).toBeDefined();
    expect(screen.getByRole("link", { name: "Start a session" }).getAttribute("href")).toBe("#workspace");
    expect(screen.getByRole("link", { name: "Explore your knowledge" }).getAttribute("href")).toBe("/sources");
    expect(screen.getByText(/AI diagnosis is not connected/)).toBeDefined();
    expect(screen.getByRole("link", { name: /Pick up the thread/ }).getAttribute("href")).toBe("/history");
    expect(screen.getByRole("link", { name: /Keep what works/ }).getAttribute("href")).toBe("/saved");
  });

  it("loads history and links to a session", async () => {
    api.listSessions.mockResolvedValue([
      {
        id: "session/1",
        title: "Async failure",
        technology: ["Python"],
        createdAt: new Date().toISOString(),
        status: "resolved",
        confidence: 90,
        errorMessage: "No event loop",
      },
    ]);

    render(<HistoryPage />);

    const link = await screen.findByRole("link", { name: /Async failure/ });
    expect(link.getAttribute("href")).toBe("/?session=session%2F1");
  });

  it("shows saved solutions returned by the service", async () => {
    api.listSaved.mockResolvedValue([
      {
        id: "saved-1",
        problem: "Port collision",
        rootCause: "Port already used",
        technology: ["Docker"],
        fixSummary: "Use a different host port.",
        sources: [],
        savedAt: new Date().toISOString(),
      },
    ]);

    render(<SavedPage />);

    expect(await screen.findByText("Port collision")).toBeDefined();
    expect(screen.getByText("Use a different host port.")).toBeDefined();
  });

  it("submits pasted documentation and prepends the new source", async () => {
    api.listKnowledgeSources.mockResolvedValue([]);
    api.addKnowledgeSource.mockResolvedValue({
      id: "source-1",
      name: "Runbook",
      kind: "docs",
      status: "uploaded",
      chunks: 0,
      updated: new Date().toISOString(),
      detail: "queued",
    });

    render(<SourcesPage />);
    fireEvent.change(screen.getByLabelText("Document title"), { target: { value: "Runbook" } });
    fireEvent.change(screen.getByLabelText("Documentation content"), {
      target: { value: "# Recovery\nRestart the worker." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add to knowledge base/ }));

    await waitFor(() =>
      expect(api.addKnowledgeSource).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "docs",
          value: "Runbook",
          content: "# Recovery\nRestart the worker.",
        }),
        expect.any(AbortSignal)
      )
    );
    expect(await screen.findByText("Runbook")).toBeDefined();
  });

  it("checks backend health and renders source status in settings", async () => {
    api.checkBackendHealth.mockResolvedValue({ status: "ok", service: "fixflow-api" });
    api.listKnowledgeSources.mockResolvedValue([
      {
        id: "source-1",
        name: "Runbook",
        kind: "docs",
        status: "indexed",
        chunks: 12,
        updated: new Date().toISOString(),
        detail: "ready",
      },
    ]);

    render(<SettingsPage />);

    expect(await screen.findByText("online")).toBeDefined();
    expect(screen.getByText("12 records")).toBeDefined();
  });

  it("polls processing sources and shows real counts without claiming indexing", async () => {
    const source = {
      id: "pending", source_id: "pending", name: "Guide.pdf", kind: "upload", source_type: "upload",
      status: "processing", document_count: 0, chunk_count: 0, chunks: 0, error_message: null,
    };
    api.listKnowledgeSources.mockResolvedValueOnce([source]).mockResolvedValue([
      { ...source, status: "ready_for_embedding", document_count: 3, chunk_count: 5, chunks: 5 },
    ]);
    render(<SourcesPage />);
    expect(await screen.findByText("Processing")).toBeDefined();
    expect(await screen.findByText("Ready for embedding", {}, { timeout: 4000 })).toBeDefined();
    expect(screen.getByText(/3 documents · 5 chunks/)).toBeDefined();
    expect(screen.queryByText("Indexed")).toBeNull();
  });

  it("shows persisted source failures and upload errors", async () => {
    api.listKnowledgeSources.mockResolvedValue([
      { id: "failed", name: "scan.pdf", kind: "upload", status: "failed", document_count: 0, chunk_count: 0,
        error_message: "No extractable text found." },
    ]);
    api.addKnowledgeSource.mockRejectedValue(new Error("Database operation unavailable"));
    render(<SourcesPage />);
    expect(await screen.findByText("No extractable text found.")).toBeDefined();
    fireEvent.change(screen.getByLabelText("Documentation content"), { target: { value: "retry" } });
    fireEvent.click(screen.getByRole("button", { name: /Add to knowledge base/ }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Database operation unavailable", "error"));
  });

  it("runs and saves a diagnosis from the main page", async () => {
    api.diagnose.mockResolvedValue(ASYNCIO_DIAGNOSIS);
    api.saveSolution.mockResolvedValue({ id: "saved-1" });

    render(<DebugSessionPage />);
    fireEvent.click(screen.getByRole("button", { name: "Run diagnosis" }));

    expect(await screen.findByText(ASYNCIO_DIAGNOSIS.rootCause)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Save diagnosis" }));
    await waitFor(() => expect(api.saveSolution).toHaveBeenCalledOnce());
    expect(navigation.router.replace).toHaveBeenCalledWith(`/?session=${ASYNCIO_DIAGNOSIS.sessionId}`);
  });

  it("clears the previous result when navigating to a new session", async () => {
    navigation.session = "existing";
    api.getSession.mockResolvedValue(ASYNCIO_DIAGNOSIS);
    const view = render(<DebugSessionPage />);
    expect(await screen.findByText(ASYNCIO_DIAGNOSIS.rootCause)).toBeDefined();
    navigation.session = null;
    view.rerender(<DebugSessionPage />);
    expect(screen.queryByText(ASYNCIO_DIAGNOSIS.rootCause)).toBeNull();
    expect(screen.getByRole("button", { name: "Run diagnosis" })).toBeDefined();
  });

  it("lets users retry a failed history load", async () => {
    api.listSessions.mockRejectedValueOnce(new Error("Backend unavailable")).mockResolvedValue([]);
    render(<HistoryPage />);
    expect(await screen.findByText("Backend unavailable")).toBeDefined();
    expect(screen.queryByText("No debug sessions yet.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
    expect(await screen.findByText("No debug sessions yet.")).toBeDefined();
  });

  it("reports degraded database readiness in settings", async () => {
    api.checkBackendHealth.mockResolvedValue({ status: "degraded", database: "connected", schema: "migration_required" });
    api.listKnowledgeSources.mockResolvedValue([]);
    render(<SettingsPage />);
    expect(await screen.findByText("degraded")).toBeDefined();
    expect(screen.getByText(/migration_required/)).toBeDefined();
    expect(screen.getByText("Not connected")).toBeDefined();
  });

  it("uploads the selected binary document without an editable text preview", async () => {
    api.listKnowledgeSources.mockResolvedValue([]);
    api.addKnowledgeSource.mockResolvedValue({
      id: "pdf", name: "Guide.pdf", kind: "upload", status: "ready_for_embedding", chunks: 1,
    });
    render(<SourcesPage />);
    fireEvent.click(screen.getByRole("tab", { name: /^Upload a file/i }));
    const file = new File(["%PDF-1.7"], "Guide.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Upload document"), { target: { files: [file] } });
    expect(screen.queryByRole("textbox", { name: "Paste document text" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Add to knowledge base/ }));
    await waitFor(() => expect(api.addKnowledgeSource).toHaveBeenCalledWith(
      expect.objectContaining({ file }), expect.any(AbortSignal),
    ));
  });

  it("keeps both uploaded and existing sources when the initial list arrives late", async () => {
    const existing = { id: "old", name: "Existing guide", status: "ready_for_embedding", kind: "docs", chunks: 1 };
    let finishList: (items: typeof existing[]) => void = () => {};
    api.listKnowledgeSources.mockImplementationOnce(() => new Promise((resolve) => { finishList = resolve; }));
    api.addKnowledgeSource.mockResolvedValue({ ...existing, id: "new", name: "New guide" });
    render(<SourcesPage />);
    fireEvent.change(screen.getByLabelText("Documentation content"), { target: { value: "New guide content" } });
    fireEvent.click(screen.getByRole("button", { name: /Add to knowledge base/ }));
    expect(await screen.findByText("New guide")).toBeDefined();
    await act(async () => finishList([existing]));
    expect(screen.getByText("Existing guide")).toBeDefined();
    expect(screen.getByText("New guide")).toBeDefined();
  });
});
