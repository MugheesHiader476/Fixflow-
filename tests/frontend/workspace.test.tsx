import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SourceComposer } from "@/components/workspace/source-composer";

const api = vi.hoisted(() => ({ addKnowledgeSource: vi.fn() }));
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

beforeEach(() => { api.addKnowledgeSource.mockReset(); toast.mockReset(); });
afterEach(cleanup);

describe("source-first workspace", () => {
  it("accepts source content through the existing ingestion API without claiming completion", async () => {
    api.addKnowledgeSource.mockResolvedValue({ id: "source", status: "uploaded" });
    render(<SourceComposer />);
    const submit = screen.getByRole("button", { name: "Add content for parsing" });
    expect(submit).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByRole("textbox", { name: "Source content" }), { target: { value: "# Runbook\nRestart the worker." } });
    fireEvent.change(screen.getByRole("textbox", { name: "Source title" }), { target: { value: "Operations" } });
    fireEvent.click(submit);
    await waitFor(() => expect(api.addKnowledgeSource).toHaveBeenCalledWith({
      kind: "docs", ingestionFormat: "document", value: "Operations", content: "# Runbook\nRestart the worker.",
    }, expect.any(AbortSignal)));
    expect(await screen.findByRole("status")).toHaveProperty("textContent", expect.stringContaining("Parsing is queued"));
    expect(screen.getByRole("link", { name: /View source status/ })).toHaveProperty("pathname", "/sources");
    expect(screen.getByRole("textbox", { name: "Source content" })).toHaveProperty("value", "");
  });

  it("retains typed content on failure and allows retry", async () => {
    api.addKnowledgeSource.mockRejectedValueOnce(new Error("Backend unavailable")).mockResolvedValueOnce({ status: "uploaded" });
    render(<SourceComposer />);
    fireEvent.change(screen.getByRole("textbox", { name: "Source content" }), { target: { value: "Useful context" } });
    fireEvent.click(screen.getByRole("button", { name: "Add content for parsing" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Backend unavailable");
    expect(screen.getByRole("textbox", { name: "Source content" })).toHaveProperty("value", "Useful context");
    fireEvent.click(screen.getByRole("button", { name: "Add content for parsing" }));
    await screen.findByRole("status");
    expect(api.addKnowledgeSource).toHaveBeenCalledTimes(2);
  });

  it("blocks duplicate submissions and cancels pending requests when unmounted", async () => {
    api.addKnowledgeSource.mockImplementation(() => new Promise(() => {}));
    const view = render(<SourceComposer />);
    fireEvent.change(screen.getByRole("textbox", { name: "Source content" }), { target: { value: "Context" } });
    const form = screen.getByRole("form", { name: "Add source content" });
    fireEvent.submit(form); fireEvent.submit(form);
    expect(api.addKnowledgeSource).toHaveBeenCalledOnce();
    const signal = api.addKnowledgeSource.mock.calls[0][1] as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
  });

  it("offers actual source destinations and reports the unavailable model honestly", () => {
    render(<SourceComposer />);
    const add = screen.getByRole("button", { name: "Add a source" });
    fireEvent.click(add);
    expect(screen.getByRole("link", { name: /Connect an app/ })).toHaveProperty("pathname", "/connectors");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(add).toHaveProperty("ariaExpanded", "false");
    expect(document.activeElement).toBe(add);
    fireEvent.click(screen.getByRole("button", { name: "Parsing only" }));
    expect(screen.getByText("Not enabled in this workspace")).toBeDefined();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByText("Not enabled in this workspace")).toBeNull();
    expect(screen.getByRole("button", { name: /Chat/ })).toHaveProperty("disabled", true);
  });
});
