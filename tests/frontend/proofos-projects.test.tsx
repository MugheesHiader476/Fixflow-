import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Projects } from "@/components/proofos/projects";
import type { ConnectorAccount, ConnectorCard, ConnectorResourcePage } from "@/lib/connector-contracts";
import { proofWorkspace } from "./fixtures/proofos";

const api = vi.hoisted(() => ({ listProjects: vi.fn(), createProject: vi.fn(), listConnectors: vi.fn(), discoverResources: vi.fn() }));
vi.mock("@/lib/proofos", () => ({ listProjects: api.listProjects, createProject: api.createProject }));
vi.mock("@/lib/connectors", () => ({ listConnectors: api.listConnectors, discoverResources: api.discoverResources }));
vi.mock("@/components/layout/app-shell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a> }));

const account: ConnectorAccount = {
  id: "github-account", provider: "github", display_name: "Selected GitHub", external_account_id: "app-user", status: "connected", authentication_status: "valid", provider_health: "ok", sync_status: "idle", last_successful_request: null, last_sync_at: null, error_message: null, progress: {}, created_at: "2026-10-25T00:00:00Z",
  configuration: { resource_ids: ["42", "43"], branches: { "42": "release", "43": "trunk" }, categories: ["actions"], start_date: null, end_date: null, attachments: false, threads: false, files: false, mime_types: [] },
};
const card: ConnectorCard = { provider: "github", configured: true, setup_message: null, install_url: null, accounts: [account, { ...account, id: "revoked", display_name: "Revoked GitHub", status: "revoked" }] };
const first: ConnectorResourcePage = { resources: [
  { id: "42", name: "example/selected", kind: "repository", parent_id: null, version: "a".repeat(40), metadata: {} },
  { id: "99", name: "example/not-selected", kind: "repository", parent_id: null, version: null, metadata: {} },
], next_cursor: "next-page" };
const second: ConnectorResourcePage = { resources: [{ id: "43", name: "example/second", kind: "repository", parent_id: null, version: "b".repeat(40), metadata: {} }], next_cursor: null };

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.listProjects.mockResolvedValue([]);
  api.createProject.mockResolvedValue(proofWorkspace().project);
  api.listConnectors.mockResolvedValue([card]);
  api.discoverResources.mockResolvedValue(first);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function openForm() {
  render(<Projects />);
  await screen.findByText(/No projects yet/);
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
}

describe("project repository selection", () => {
  it("associates only a selected repository, its configured branch and explicit customer authority", async () => {
    api.discoverResources.mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    await openForm();
    expect(screen.queryByRole("option", { name: "Revoked GitHub" })).toBeNull();
    fireEvent.change(screen.getByLabelText("GitHub connection (optional)"), { target: { value: account.id } });
    await screen.findByRole("option", { name: "example/selected" });
    expect(screen.queryByRole("option", { name: "example/not-selected" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Load more repositories" }));
    await screen.findByRole("option", { name: "example/second" });
    expect(api.discoverResources).toHaveBeenLastCalledWith(account.id, "next-page");
    fireEvent.change(screen.getByLabelText("Selected repository"), { target: { value: "43" } });
    expect(screen.getByLabelText("Branch")).toHaveProperty("value", "trunk");
    fireEvent.change(screen.getByLabelText("Branch"), { target: { value: "release" } });
    fireEvent.change(screen.getByLabelText("Selected repository"), { target: { value: "42" } });
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Website" } });
    fireEvent.change(screen.getByLabelText("Customer name"), { target: { value: "Fictional customer" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Approved delivery" } });
    fireEvent.change(screen.getByLabelText(/Customer approver account ID/), { target: { value: "user_customer" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith({ name: "Website", customer_name: "Fictional customer", description: "Approved delivery", customer_approver_id: "user_customer", github_account_id: account.id, repository_id: "42", repository_name: "example/selected", branch: "release" }));
    await waitFor(() => expect(screen.queryByLabelText("Project name")).toBeNull());
  });

  it("retains selected results when pagination fails and clears authority on disconnect selection", async () => {
    api.discoverResources.mockResolvedValueOnce(first).mockRejectedValueOnce("provider unavailable").mockRejectedValueOnce(new Error("Consent expired"));
    await openForm();
    fireEvent.change(screen.getByLabelText("GitHub connection (optional)"), { target: { value: account.id } });
    await screen.findByRole("option", { name: "example/selected" });
    fireEvent.click(screen.getByRole("button", { name: "Load more repositories" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Could not load more repositories.");
    expect(screen.getByRole("option", { name: "example/selected" })).toBeDefined();
    fireEvent.change(screen.getByLabelText("GitHub connection (optional)"), { target: { value: "" } });
    expect(screen.queryByLabelText("Selected repository")).toBeNull();
    fireEvent.change(screen.getByLabelText("GitHub connection (optional)"), { target: { value: account.id } });
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Consent expired");
    expect(screen.queryByRole("option", { name: "example/selected" })).toBeNull();
  });

  it("does not restore repository choices from a cancelled discovery response", async () => {
    const pending = Promise.withResolvers<ConnectorResourcePage>();
    api.discoverResources.mockReturnValueOnce(pending.promise);
    await openForm();
    fireEvent.change(screen.getByLabelText("GitHub connection (optional)"), { target: { value: account.id } });
    await waitFor(() => expect(api.discoverResources).toHaveBeenCalledOnce());
    const signal = api.discoverResources.mock.calls[0][2] as AbortSignal;
    fireEvent.change(screen.getByLabelText("GitHub connection (optional)"), { target: { value: "" } });
    expect(signal.aborted).toBe(true);
    await act(async () => { pending.resolve(first); });
    expect(screen.queryByRole("option", { name: "example/selected" })).toBeNull();
    expect(api.createProject).not.toHaveBeenCalled();
  });

  it("retries project loading and exposes connector failures without inventing repository access", async () => {
    api.listProjects.mockRejectedValueOnce(new Error("Gateway unavailable")).mockResolvedValueOnce([proofWorkspace().project]);
    api.listConnectors.mockRejectedValue(new Error("Provider unavailable"));
    render(<Projects />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Gateway unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Customer Website" });
    fireEvent.click(screen.getByRole("button", { name: "New project" }));
    expect(screen.getByText(/Could not load connections/)).toBeDefined();
    expect(screen.queryByRole("option", { name: "Selected GitHub" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Project name")).toBeNull();
  });

  it("keeps a rejected project form for correction rather than showing success", async () => {
    api.createProject.mockRejectedValue(new Error("Repository is no longer selected"));
    await openForm();
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Website" } });
    fireEvent.change(screen.getByLabelText("Customer name"), { target: { value: "Customer" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Repository is no longer selected");
    expect(screen.getByLabelText("Project name")).toHaveProperty("value", "Website");
    expect(screen.queryByText(/Project created\./)).toBeNull();
  });
});
