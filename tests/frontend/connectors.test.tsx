import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConnectorsPage from "@/app/connectors/page";
import type { ConnectorAccount, ConnectorCard } from "@/lib/connector-contracts";
import { authorizationUrl, validConnectorResponse } from "@/lib/connector-contracts";

const api = vi.hoisted(() => ({ listConnectors: vi.fn(), connectProvider: vi.fn(), discoverResources: vi.fn(), configureConnector: vi.fn(), syncConnector: vi.fn(), disconnectConnector: vi.fn(), checkConnector: vi.fn() }));
vi.mock("@/lib/connectors", () => api);
vi.mock("@/components/layout/app-shell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));

const ACCOUNT: ConnectorAccount = {
  id: "28d6ce92-c958-4010-b4ba-1784cf82dc10", provider: "gmail", display_name: "person@example.test", external_account_id: "external",
  status: "connected", authentication_status: "valid", provider_health: "available", sync_status: "idle", last_sync_at: null,
  last_successful_request: null, error_message: null, progress: {}, created_at: "2026-10-02T00:00:00Z",
  configuration: { resource_ids: [], branches: {}, categories: ["code", "issues"], start_date: null, end_date: null, attachments: false, threads: true, files: false, mime_types: [] },
};
const CARDS: ConnectorCard[] = ["gmail", "github", "google_drive", "slack"].map((provider) => ({ provider: provider as ConnectorCard["provider"], configured: provider === "gmail", setup_message: provider === "gmail" ? null : "Server credentials are required", accounts: provider === "gmail" ? [ACCOUNT] : [], install_url: null }));

beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.setAttribute("open", ""); } });
  api.listConnectors.mockResolvedValue(CARDS);
  api.discoverResources.mockResolvedValue({ resources: [{ id: "INBOX", name: "Inbox", kind: "label", parent_id: null, version: null, metadata: {} }], next_cursor: "second" });
  api.configureConnector.mockResolvedValue(ACCOUNT);
  api.syncConnector.mockResolvedValue(ACCOUNT);
  api.checkConnector.mockResolvedValue(ACCOUNT);
  api.disconnectConnector.mockResolvedValue({ ...ACCOUNT, status: "disconnected" });
});
afterEach(cleanup);

describe("Connected Apps", () => {
  it("renders all providers and disables unconfigured authorization", async () => {
    render(<ConnectorsPage />);
    await screen.findByRole("heading", { name: "Gmail" });
    expect(screen.getByRole("heading", { name: "Google Drive" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Connect Slack" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Sync now" })).toHaveProperty("disabled", true);
  });

  it("discovers pages, selects labels, and saves actual options", async () => {
    render(<ConnectorsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Configure resources" }));
    fireEvent.click(await screen.findByLabelText(/Inbox/));
    fireEvent.click(screen.getByLabelText("Include attachments"));
    api.discoverResources.mockResolvedValueOnce({ resources: [{ id: "SENT", name: "Sent", kind: "label" }], next_cursor: null });
    fireEvent.click(screen.getByRole("button", { name: "Load more resources" }));
    await screen.findByLabelText(/Sent/);
    expect(api.discoverResources).toHaveBeenLastCalledWith(ACCOUNT.id, "second", expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "Save selection" }));
    await waitFor(() => expect(api.configureConnector).toHaveBeenCalledWith(ACCOUNT.id, expect.objectContaining({ resource_ids: ["INBOX"], attachments: true })));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("preserves typed MIME separators and submits separate Drive file types", async () => {
    const drive = { ...ACCOUNT, provider: "google_drive" as const };
    api.listConnectors.mockResolvedValue([{ ...CARDS[2], configured: true, accounts: [drive] }]);
    render(<ConnectorsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Configure resources" }));
    fireEvent.click(await screen.findByLabelText(/Inbox/));
    const input = screen.getByLabelText("File MIME types (optional, comma separated)");
    fireEvent.change(input, { target: { value: "application/pdf," } });
    expect(input).toHaveProperty("value", "application/pdf,");
    fireEvent.change(input, { target: { value: "application/pdf, text/plain" } });
    fireEvent.click(screen.getByRole("button", { name: "Save selection" }));
    await waitFor(() => expect(api.configureConnector).toHaveBeenCalledWith(ACCOUNT.id, expect.objectContaining({ mime_types: ["application/pdf", "text/plain"] })));
  });

  it("requires explicit disconnect confirmation and supports purge", async () => {
    render(<ConnectorsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(api.disconnectConnector).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Permanently purge connected sources and chunks"));
    expect(screen.getByText("Purging cannot be undone.")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Confirm disconnect" }));
    await waitFor(() => expect(api.disconnectConnector).toHaveBeenCalledWith(ACCOUNT.id, "purge"));
  });

  it("exposes failure and reconnection without treating queued resources as indexed", async () => {
    api.listConnectors.mockResolvedValue([{ ...CARDS[0], accounts: [{ ...ACCOUNT, status: "reauth_required", error_message: "Reconnect to continue", sync_status: "failed", progress: { queued: 2 } }] }]);
    render(<ConnectorsPage />);
    expect(await screen.findByText("Reconnect to continue")).toBeDefined();
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeDefined();
    expect(screen.getByText(/Queued for ingestion: 2/)).toBeDefined();
    expect(screen.queryByText("Indexed")).toBeNull();
  });

  it("preserves a resource discovery error and supports cancellation", async () => {
    api.discoverResources.mockRejectedValue(new Error("Provider rate limit reached"));
    render(<ConnectorsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Configure resources" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Provider rate limit reached");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.configureConnector).not.toHaveBeenCalled();
  });

  it("checks provider authorization and refreshes a failed operation's status", async () => {
    api.checkConnector.mockRejectedValue(new Error("Authorization expired; reconnect"));
    render(<ConnectorsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Check connection" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Authorization expired; reconnect");
    expect(api.checkConnector).toHaveBeenCalledWith(ACCOUNT.id);
    expect(api.listConnectors.mock.calls.length).toBeGreaterThan(1);
  });

  it("can clear every resource to stop synchronization", async () => {
    api.listConnectors.mockResolvedValue([{ ...CARDS[0], accounts: [{ ...ACCOUNT, configuration: { ...ACCOUNT.configuration, resource_ids: ["INBOX"] } }] }]);
    render(<ConnectorsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Configure resources" }));
    fireEvent.click(await screen.findByLabelText(/Inbox/));
    fireEvent.click(screen.getByRole("button", { name: "Save selection" }));
    await waitFor(() => expect(api.configureConnector).toHaveBeenCalledWith(ACCOUNT.id, expect.objectContaining({ resource_ids: [] })));
  });
});

describe("connector response validation", () => {
  it("accepts complete contracts and rejects malformed health and selections", () => {
    expect(validConnectorResponse("/api/connectors", CARDS)).toBe(true);
    expect(validConnectorResponse(`/api/connectors/${ACCOUNT.id}/status`, { ...ACCOUNT, progress: { queued: -1 } })).toBe(false);
    expect(validConnectorResponse(`/api/connectors/${ACCOUNT.id}/status`, { ...ACCOUNT, configuration: { resource_ids: [] } })).toBe(false);
    expect(validConnectorResponse("/api/connectors/gmail/connect", { authorization_url: "javascript:alert(1)" })).toBe(false);
    expect(authorizationUrl("https://accounts.google.com/o/oauth2/v2/auth?state=state")).toBe(true);
    expect(authorizationUrl("https://accounts.google.com.attacker.test/o/oauth2/v2/auth")).toBe(false);
    expect(validConnectorResponse(`/api/connectors/${ACCOUNT.id}/query`, { resources: [], count: 0, exact: true, next_cursor: null })).toBe(true);
    expect(validConnectorResponse(`/api/connectors/${ACCOUNT.id}/query`, { resources: [], count: -1, exact: true, next_cursor: null })).toBe(false);
  });
});
