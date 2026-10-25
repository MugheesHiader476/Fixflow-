import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Approvals } from "@/components/proofos/approvals";
import { Projects } from "@/components/proofos/projects";
import { ProjectWorkspaceView } from "@/components/proofos/workspace";
import { money } from "@/components/proofos/shared";
import { validProofResponse } from "@/lib/proofos-contracts";
import { validResponse } from "@/lib/response-validation";
import { approvalRecord, criterion, milestoneId, packageRecord, projectId, proofWorkspace, requirementId, sourceId } from "./fixtures/proofos";

const api = vi.hoisted(() => ({ listProjects: vi.fn(), createProject: vi.fn(), projectWorkspace: vi.fn(), extractContract: vi.fn(), approveContract: vi.fn(), reviewRequirement: vi.fn(), assignRequirement: vi.fn(), createMilestone: vi.fn(), approveMilestone: vi.fn(), collectEvidence: vi.fn(), addMilestoneEvidence: vi.fn(), verifyMilestone: vi.fn(), preparePackage: vi.fn(), packageDetail: vi.fn(), reviewPackage: vi.fn(), requestApproval: vi.fn(), listApprovals: vi.fn(), approvalDetail: vi.fn(), decideApproval: vi.fn(), prepareInvoice: vi.fn(), cancelProofJob: vi.fn(), taskPacket: vi.fn(), packageDownload: vi.fn((id: string, audience: string, format: string) => `/api/backend/api/proofos/packages/${id}/download/${audience}/${format}`), approvalDownload: vi.fn((id: string, format: string) => `/api/backend/api/proofos/approvals/${id}/download/${format}`) }));
const sources = vi.hoisted(() => vi.fn());
vi.mock("@/lib/proofos", () => api);
vi.mock("@/lib/api", () => ({ listKnowledgeSources: sources }));
vi.mock("@/lib/connectors", () => ({ listConnectors: vi.fn().mockResolvedValue([]), discoverResources: vi.fn() }));
vi.mock("@/components/layout/app-shell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a> }));

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockClear());
  api.projectWorkspace.mockResolvedValue(proofWorkspace());
  api.listProjects.mockResolvedValue([]);
  api.listApprovals.mockResolvedValue([]);
  sources.mockReset().mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("ProofOS response contracts", () => {
  it.each([[1000000, "USD", "10,000.00"], [10000, "JPY", "10,000"], [10000, "KWD", "10.000"]] as const)("renders %s %s minor units without assuming every currency has cents", (amount, currency, expected) => {
    expect(money(amount, currency)).toContain(expected);
  });
  it("accepts the complete typed workspace and rejects invented status or missing provenance", () => {
    const value = proofWorkspace();
    expect(validProofResponse(`/api/proofos/projects/${projectId}`, value)).toBe(true);
    expect(validProofResponse(`/api/proofos/projects/${projectId}`, { ...value, evidence: [{ ...value.evidence[0], fact: { ...value.evidence[0].fact, observed_at: undefined } }] })).toBe(false);
    expect(validProofResponse(`/api/proofos/projects/${projectId}`, { ...value, evidence: [{ ...value.evidence[0], current: "yes" }] })).toBe(false);
    expect(validProofResponse(`/api/proofos/projects/${projectId}`, { ...value, milestones: [{ ...value.milestones[0], latest_verification: { ...value.verifications[0], result: { ...value.verifications[0].result, technical_status: "COMPLETE" } } }] })).toBe(false);
  });
  it("routes ProofOS responses before historical fallback and rejects unsupported paths", () => {
    expect(validResponse("/api/proofos/projects", [proofWorkspace().project])).toBe(true);
    expect(validResponse("/api/proofos/projects", proofWorkspace().project, "POST")).toBe(true);
    expect(validProofResponse("/api/proofos/approvals", [approvalRecord])).toBe(true);
    expect(validProofResponse("/api/proofos/projects/delete-all", {})).toBe(false);
    expect(validProofResponse(`/api/proofos/milestones/${milestoneId}/invoice-draft`, { amount_minor: 1000000 })).toBe(false);
  });
});

describe("ProofOS project workflow", () => {
  it("shows an actionable empty state and creates a project without inventing repository authority", async () => {
    api.createProject.mockResolvedValue(proofWorkspace().project);
    render(<Projects />);
    expect(await screen.findByText(/No projects yet/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "New project" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Customer Website" } });
    fireEvent.change(screen.getByLabelText("Customer name"), { target: { value: "Fictional Customer" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith(expect.objectContaining({ name: "Customer Website", github_account_id: null, repository_id: null, customer_approver_id: null })));
  });
  it("renders project access failures safely without retained private content", async () => {
    api.projectWorkspace.mockRejectedValue(new Error("Project access was revoked"));
    render(<ProjectWorkspaceView projectId={projectId} />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Project access was revoked"));
    expect(screen.queryByText("Fictional acceptance fixture")).toBeNull();
  });
  it("keeps failing tests, missing documentation and pending acceptance visible and blocks invoices", async () => {
    render(<ProjectWorkspaceView projectId={projectId} />);
    await screen.findByRole("heading", { level: 1, name: "Customer Website" });
    fireEvent.click(screen.getByRole("tab", { name: "Milestones" }));
    expect(screen.getByText("Mandatory authentication test failed for the applicable SHA.")).toBeDefined();
    expect(screen.getByText("Delivery documentation is missing.")).toBeDefined();
    expect(screen.getByText("Customer approval is pending")).toBeDefined();
    expect(screen.getByRole("button", { name: "Prepare invoice draft" }).hasAttribute("disabled")).toBe(true);
    expect(api.prepareInvoice).not.toHaveBeenCalled();
  });
  it("distinguishes technical success from pending contractual acceptance", async () => {
    const value = proofWorkspace();
    value.verifications[0].result.technical_status = "PASSED";
    value.verifications[0].result.delivery_status = "PASSED";
    value.verifications[0].result.checks = [];
    value.milestones[0].readiness.reasons = ["Customer approval is pending"];
    api.projectWorkspace.mockResolvedValue(value);
    render(<ProjectWorkspaceView projectId={projectId} />);
    await screen.findByRole("heading", { name: "Customer Website" });
    fireEvent.click(screen.getByRole("tab", { name: "Milestones" }));
    expect(screen.getAllByText("PASSED")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Prepare invoice draft" }).hasAttribute("disabled")).toBe(true);
  });
  it("preserves original wording and blocks approval while ambiguities remain", async () => {
    api.reviewRequirement.mockResolvedValue(proofWorkspace().requirements[0]);
    render(<ProjectWorkspaceView projectId={projectId} />);
    await screen.findByRole("heading", { name: "Customer Website" });
    fireEvent.click(screen.getByRole("tab", { name: "SRS & requirements" }));
    expect(screen.getByRole("table").textContent).toContain("Mandatory authentication test failed for the applicable SHA.");
    fireEvent.click(screen.getByText("REQ-001", { selector: "summary span" }));
    expect(screen.getByRole("button", { name: "Approve requirement" }).hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("Proposed interpretation"), { target: { value: "Owner corrected the expected test behavior." } });
    fireEvent.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(api.reviewRequirement).toHaveBeenCalledWith(requirementId, expect.objectContaining({ interpretation: "Owner corrected the expected test behavior.", acceptance_criteria: [criterion], approve: false })));
    expect(api.reviewRequirement.mock.calls[0][1]).not.toHaveProperty("original_text");
  });
  it("only offers parsed accessible sources for contract extraction", async () => {
    sources.mockResolvedValue([{ id: sourceId, name: "Allowed SRS", status: "parsed", access_available: true, is_active: true }, { id: "blocked", name: "Revoked contract", status: "parsed", access_available: false }, { id: "pending", name: "Still parsing", status: "processing", access_available: true }]);
    render(<ProjectWorkspaceView projectId={projectId} />);
    await screen.findByRole("heading", { name: "Customer Website" });
    fireEvent.click(screen.getByRole("tab", { name: "SRS & requirements" }));
    expect(await screen.findByRole("option", { name: "Allowed SRS" })).toBeDefined();
    expect(screen.queryByRole("option", { name: "Revoked contract" })).toBeNull();
    expect(screen.queryByRole("option", { name: "Still parsing" })).toBeNull();
  });
  it("shows evidence provenance and never renders executable source URLs", async () => {
    const value = proofWorkspace(); value.evidence[0].fact.url = "javascript:alert('untrusted')"; value.evidence[0].current = false;
    api.projectWorkspace.mockResolvedValue(value);
    render(<ProjectWorkspaceView projectId={projectId} />);
    await screen.findByRole("heading", { name: "Customer Website" });
    fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
    expect(screen.getByText("Authentication tests")).toBeDefined();
    expect(screen.getByText("HISTORICAL")).toBeDefined();
    expect(screen.getByText("a".repeat(40))).toBeDefined();
    expect(screen.queryByRole("link", { name: "Open original resource ↗" })).toBeNull();
  });
  it("binds internal review to the exact immutable internal package hash", async () => {
    const value = proofWorkspace(); value.packages = [packageRecord];
    api.projectWorkspace.mockResolvedValue(value); api.reviewPackage.mockResolvedValue({ ...packageRecord, internal_review: "approved" });
    render(<ProjectWorkspaceView projectId={projectId} />);
    await screen.findByRole("heading", { name: "Customer Website" });
    fireEvent.click(screen.getByRole("tab", { name: "Packages & approvals" }));
    fireEvent.change(screen.getByLabelText("Internal review reason"), { target: { value: "Reviewed the exact package and outstanding checks." } });
    fireEvent.click(screen.getByRole("button", { name: "Approve internal review" }));
    await waitFor(() => expect(api.reviewPackage).toHaveBeenCalledWith(packageRecord.id, packageRecord.internal_hash, "approved", "Reviewed the exact package and outstanding checks."));
    expect(screen.getByRole("link", { name: "Customer PDF" }).getAttribute("href")).toContain(`/packages/${packageRecord.id}/download/customer/pdf`);
  });
  it("stops status polling after three failures and preserves the last job state", async () => {
    vi.useFakeTimers();
    const value = proofWorkspace(); value.jobs = [{ id: sourceId, project_id: projectId, kind: "verify", status: "queued", error: null, result: {}, created_at: "2026-10-25T00:00:00Z" }];
    api.projectWorkspace.mockResolvedValueOnce(value).mockRejectedValue(new Error("Status unavailable"));
    render(<ProjectWorkspaceView projectId={projectId} />);
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
    expect(api.projectWorkspace).toHaveBeenCalledTimes(4);
    expect(screen.getByText(/Automatic refresh paused after three failures/)).toBeDefined();
    expect(screen.getByRole("heading", { name: "Customer Website" })).toBeDefined();
    api.projectWorkspace.mockResolvedValue(value);
    fireEvent.click(screen.getByRole("button", { name: "Refresh project" }));
    await act(async () => {});
    expect(screen.queryByText(/Automatic refresh paused/)).toBeNull();
  });
});

describe("authenticated customer approval", () => {
  it("requires loading the customer-safe package before an exact-hash decision", async () => {
    api.listApprovals.mockResolvedValue([approvalRecord]);
    api.approvalDetail.mockResolvedValue({ approval: approvalRecord, package: { title: "Customer-safe delivery", internal_notes: null } });
    api.decideApproval.mockResolvedValue({ ...approvalRecord, status: "accepted" });
    render(<Approvals />);
    await screen.findByRole("heading", { name: "Customer Website" });
    expect(screen.getByRole("button", { name: "Accept package" }).closest("fieldset")?.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Review customer package" }));
    await screen.findByText("Customer-safe delivery");
    fireEvent.change(screen.getByLabelText("Decision reason"), { target: { value: "I accept this exact delivery package." } });
    fireEvent.click(screen.getByRole("button", { name: "Accept package" }));
    await waitFor(() => expect(api.decideApproval).toHaveBeenCalledWith(approvalRecord.id, approvalRecord.package_hash, "accepted", "I accept this exact delivery package."));
  });
  it("disables expired review requests and shows recipient-safe failures", async () => {
    api.listApprovals.mockResolvedValue([{ ...approvalRecord, expires_at: "2000-01-01T00:00:00Z" }]);
    api.approvalDetail.mockRejectedValue(new Error("Approval request is no longer accessible"));
    render(<Approvals />);
    await screen.findByText(/This approval request has expired/);
    const section = screen.getByRole("button", { name: "Accept package" }).closest("fieldset");
    expect(section?.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Review customer package" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("no longer accessible"));
    expect(api.decideApproval).not.toHaveBeenCalled();
    expect(within(screen.getByRole("main")).queryByText("Customer-safe delivery")).toBeNull();
  });
});
