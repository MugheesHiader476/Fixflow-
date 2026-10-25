import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectWorkspaceView } from "@/components/proofos/workspace";
import { downloadJson } from "@/components/proofos/milestones";
import { StructuredPreview } from "@/components/proofos/packages";
import { contractId, milestoneId, packageRecord, projectId, proofWorkspace, requirementId, sourceId } from "./fixtures/proofos";

const api = vi.hoisted(() => ({ projectWorkspace: vi.fn(), extractContract: vi.fn(), approveContract: vi.fn(), reviewRequirement: vi.fn(), assignRequirement: vi.fn(), createMilestone: vi.fn(), approveMilestone: vi.fn(), collectEvidence: vi.fn(), addMilestoneEvidence: vi.fn(), verifyMilestone: vi.fn(), preparePackage: vi.fn(), packageDetail: vi.fn(), reviewPackage: vi.fn(), requestApproval: vi.fn(), prepareInvoice: vi.fn(), cancelProofJob: vi.fn(), taskPacket: vi.fn(), packageDownload: vi.fn(() => "/private-package") }));
const sources = vi.hoisted(() => vi.fn());
vi.mock("@/lib/proofos", () => api);
vi.mock("@/lib/api", () => ({ listKnowledgeSources: sources }));
vi.mock("@/lib/connectors", () => ({ listConnectors: vi.fn().mockResolvedValue([]) }));
vi.mock("@/components/layout/app-shell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock("next/link", () => ({ default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a> }));

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset().mockResolvedValue({}));
  api.projectWorkspace.mockResolvedValue(proofWorkspace());
  sources.mockReset().mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function openTab(name: string) {
  render(<ProjectWorkspaceView projectId={projectId} />);
  await screen.findByRole("heading", { name: "Customer Website", level: 1 });
  fireEvent.click(screen.getByRole("tab", { name }));
}

describe("reviewed ProofOS workflow interactions", () => {
  it("renders customer JSON types as inert text and never executes task packets", () => {
    render(<StructuredPreview value={{ missing: null, yes: true, no: false, empty: [], items: ["<script>untrusted</script>", { nested: "text" }] }} />);
    expect(screen.getByText("Not supplied")).toBeDefined();
    expect(screen.getByText("Yes")).toBeDefined();
    expect(screen.getByText("No")).toBeDefined();
    expect(screen.getByText("None recorded")).toBeDefined();
    expect(screen.getByText("<script>untrusted</script>")).toBeDefined();
    expect(document.querySelector("script")).toBeNull();
    const create = vi.fn<(value: Blob) => string>().mockReturnValue("blob:task-packet");
    const revoke = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    downloadJson({ execution_authorized: false }, "reviewed-task.json");
    expect(create.mock.calls[0][0]).toBeInstanceOf(Blob);
    const anchor = click.mock.contexts[0];
    if (!(anchor instanceof HTMLAnchorElement)) throw new Error("Task download did not use an anchor");
    expect(anchor.download).toBe("reviewed-task.json");
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:task-packet");
    expect(api.taskPacket).not.toHaveBeenCalled();
  });

  it("edits acceptance policy and engineering references without replacing original wording", async () => {
    await openTab("SRS & requirements");
    fireEvent.click(screen.getByText("REQ-001", { selector: "summary span" }));
    fireEvent.change(screen.getByLabelText("Unresolved ambiguities (one per line)"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Acceptance condition"), { target: { value: "Staging deployment is verified" } });
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "delivery" } });
    fireEvent.change(screen.getByLabelText("Evidence policy"), { target: { value: "deployment" } });
    fireEvent.change(screen.getByLabelText("Repository ID"), { target: { value: "43" } });
    fireEvent.change(screen.getByLabelText("Required branch"), { target: { value: "release" } });
    fireEvent.change(screen.getByLabelText("Required commit SHA"), { target: { value: "b".repeat(40) } });
    fireEvent.change(screen.getByLabelText("Workflow ID"), { target: { value: "9" } });
    fireEvent.change(screen.getByLabelText("Required run ID"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Test suite"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Deployment environment"), { target: { value: "staging" } });
    fireEvent.change(screen.getByLabelText("Evidence freshness (hours)"), { target: { value: "24" } });
    fireEvent.change(screen.getByLabelText("Required test case names (one per line)"), { target: { value: "smoke\n\ncheckout" } });
    fireEvent.click(screen.getByLabelText("Mandatory condition"));
    fireEvent.click(screen.getByLabelText("Allow skipped tests"));
    fireEvent.click(screen.getByRole("button", { name: "Add acceptance condition" }));
    fireEvent.change(screen.getAllByLabelText("Acceptance condition")[1], { target: { value: "Temporary condition" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Remove condition" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Add engineering reference" }));
    fireEvent.change(screen.getByLabelText("Reference type"), { target: { value: "pull_request" } });
    fireEvent.change(screen.getByLabelText("External ID"), { target: { value: "18" } });
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Release change" } });
    fireEvent.change(screen.getByLabelText("HTTPS source URL (optional)"), { target: { value: "https://github.com/example/repo/pull/18" } });
    fireEvent.click(screen.getByRole("button", { name: "Add engineering reference" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Remove reference" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Approve requirement" }));
    await waitFor(() => expect(api.reviewRequirement).toHaveBeenCalledWith(requirementId, expect.objectContaining({
      approve: true, ambiguities: [],
      acceptance_criteria: [expect.objectContaining({ description: "Staging deployment is verified", category: "delivery", kind: "deployment", repository_id: "43", branch: "release", sha: "b".repeat(40), workflow_id: 9, run_id: null, suite: null, environment: "staging", cases: ["smoke", "checkout"], max_age_hours: 24, mandatory: false, allow_skipped: true })],
      engineering_links: [{ kind: "pull_request", external_id: "18", title: "Release change", url: "https://github.com/example/repo/pull/18" }],
    })));
    expect(screen.getByRole("blockquote").textContent).toBe("Authentication must pass all mandatory tests.");
  });

  it("extracts a selected parsed source and explicitly assigns a reviewed requirement", async () => {
    sources.mockResolvedValue([{ id: sourceId, name: "Delivery agreement", status: "parsed", access_available: true, is_active: true }]);
    const data = proofWorkspace();
    data.requirements[0].milestone_id = null;
    data.requirements[0].state = "APPROVED";
    api.projectWorkspace.mockResolvedValue(data);
    await openTab("SRS & requirements");
    fireEvent.change(screen.getByLabelText("Parsed source"), { target: { value: sourceId } });
    fireEvent.change(screen.getByLabelText("Contract or SRS title"), { target: { value: "Reviewed contract" } });
    fireEvent.click(screen.getByRole("button", { name: "Extract requirements" }));
    await waitFor(() => expect(api.extractContract).toHaveBeenCalledWith(projectId, sourceId, "Reviewed contract"));
    fireEvent.click(screen.getByRole("button", { name: "Approve contract version" }));
    await waitFor(() => expect(api.approveContract).toHaveBeenCalledWith(contractId));
    fireEvent.click(screen.getByText("REQ-001", { selector: "summary span" }));
    fireEvent.change(screen.getByLabelText("Milestone assignment"), { target: { value: milestoneId } });
    fireEvent.click(screen.getByRole("button", { name: "Assign requirement" }));
    await waitFor(() => expect(api.assignRequirement).toHaveBeenCalledWith(requirementId, milestoneId));
  });

  it("creates a milestone from explicit contractual fields and closes only after success", async () => {
    await openTab("Milestones");
    fireEvent.click(screen.getByRole("button", { name: "Add milestone" }));
    fireEvent.change(screen.getByLabelText("Contract revision"), { target: { value: contractId } });
    fireEvent.change(screen.getByLabelText("Milestone code"), { target: { value: "M-03" } });
    fireEvent.change(screen.getByLabelText("Milestone title"), { target: { value: "Reviewed delivery" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Release staging" } });
    fireEvent.change(screen.getByLabelText("Applicable commit SHA"), { target: { value: "b".repeat(40) } });
    fireEvent.change(screen.getByLabelText("Contract amount in minor units (optional)"), { target: { value: "1000000" } });
    fireEvent.change(screen.getByLabelText("Currency (optional)"), { target: { value: "usd" } });
    fireEvent.change(screen.getByLabelText("Contractual payment terms (optional)"), { target: { value: "Net 30" } });
    fireEvent.click(screen.getByRole("button", { name: "Create milestone" }));
    await waitFor(() => expect(api.createMilestone).toHaveBeenCalledWith(projectId, { contract_id: contractId, code: "M-03", title: "Reviewed delivery", description: "Release staging", expected_sha: "b".repeat(40), amount_minor: 1000000, currency: "USD", payment_terms: "Net 30" }));
    await waitFor(() => expect(screen.queryByLabelText("Milestone code")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Add milestone" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Milestone code")).toBeNull();
  });

  it("keeps the milestone form open and exposes safe gateway failure", async () => {
    api.createMilestone.mockRejectedValue(new Error("Contract version is unavailable"));
    await openTab("Milestones");
    fireEvent.click(screen.getByRole("button", { name: "Add milestone" }));
    fireEvent.change(screen.getByLabelText("Contract revision"), { target: { value: contractId } });
    fireEvent.change(screen.getByLabelText("Milestone code"), { target: { value: "M-03" } });
    fireEvent.change(screen.getByLabelText("Milestone title"), { target: { value: "Delivery" } });
    fireEvent.click(screen.getByRole("button", { name: "Create milestone" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Contract version is unavailable");
    expect(screen.getByLabelText("Milestone code")).toHaveProperty("value", "M-03");
  });

  it("freezes scope, queues verification/packages and requests only an eligible invoice draft", async () => {
    const data = proofWorkspace();
    data.milestones[0].scope_approved = false;
    data.milestones[0].readiness = { eligible: true, reasons: [] };
    data.packages = [packageRecord, { ...packageRecord, id: "latest", version: 2, customer_status: "accepted" }];
    api.projectWorkspace.mockResolvedValue(data);
    await openTab("Milestones");
    fireEvent.click(screen.getByRole("button", { name: "Approve & freeze scope" }));
    await waitFor(() => expect(api.approveMilestone).toHaveBeenCalledWith(milestoneId));
    data.milestones[0].scope_approved = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh project" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Approve & freeze scope" })).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Verify milestone" }));
    await waitFor(() => expect(api.verifyMilestone).toHaveBeenCalledWith(milestoneId));
    fireEvent.click(screen.getByRole("button", { name: "Prepare acceptance package" }));
    await waitFor(() => expect(api.preparePackage).toHaveBeenCalledWith(milestoneId));
    fireEvent.click(screen.getByRole("button", { name: "Prepare invoice draft" }));
    await waitFor(() => expect(api.prepareInvoice).toHaveBeenCalledWith(milestoneId));
    expect(screen.getByText(/Latest recorded customer state: accepted/)).toBeDefined();
  });

  it("queues exact GitHub evidence and retains source discussion as internal unless selected", async () => {
    const data = proofWorkspace();
    data.project.repository_id = "42";
    data.project.repository_name = "example/website";
    api.projectWorkspace.mockResolvedValue(data);
    sources.mockResolvedValue([{ id: sourceId, name: "Delivery guide", status: "parsed", access_available: true, is_active: true }]);
    await openTab("Evidence");
    fireEvent.change(screen.getByLabelText("Workflow ID"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Actions run ID"), { target: { value: "18" } });
    fireEvent.change(screen.getByLabelText("Test artifact names (one per line, up to five)"), { target: { value: "junit\n\ncoverage" } });
    fireEvent.click(screen.getByRole("button", { name: "Collect native GitHub evidence" }));
    await waitFor(() => expect(api.collectEvidence).toHaveBeenCalledWith(milestoneId, { run_id: 18, workflow_id: 7, artifact_names: ["junit", "coverage"] }));
    fireEvent.change(screen.getByLabelText("Evidence source"), { target: { value: sourceId } });
    fireEvent.change(screen.getByLabelText("Evidence kind"), { target: { value: "discussion" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /REQ-001/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /REQ-001/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /REQ-001/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add source evidence" }));
    await waitFor(() => expect(api.addMilestoneEvidence).toHaveBeenCalledWith(milestoneId, { source_id: sourceId, requirement_ids: [requirementId], kind: "discussion", customer_safe: false }));
    fireEvent.change(screen.getByLabelText("Filter source or title"), { target: { value: "unmatched" } });
    expect(screen.getByText("No evidence matches these filters.")).toBeDefined();
    fireEvent.change(screen.getByLabelText("Filter source or title"), { target: { value: "github" } });
    fireEvent.change(screen.getByLabelText("Evidence status"), { target: { value: "passed" } });
    expect(screen.getByText("No evidence matches these filters.")).toBeDefined();
    fireEvent.change(screen.getByLabelText("Evidence status"), { target: { value: "failed" } });
    fireEvent.change(screen.getByLabelText("Requirement"), { target: { value: requirementId } });
    expect(screen.getByText("Authentication tests")).toBeDefined();
  });

  it("previews exact customer/internal package variants and requests explicit approval", async () => {
    const data = proofWorkspace();
    data.packages = [{ ...packageRecord, internal_review: "approved" }];
    api.projectWorkspace.mockResolvedValue(data);
    api.packageDetail.mockResolvedValue({ package: packageRecord, customer: { visible: "Customer delivery" }, internal: { visible: "Internal notes" } });
    await openTab("Packages & approvals");
    fireEvent.click(screen.getByRole("button", { name: "Preview package" }));
    expect(await screen.findByText("Customer delivery")).toBeDefined();
    expect(screen.queryByText("Internal notes")).toBeNull();
    fireEvent.change(screen.getByLabelText("Package audience"), { target: { value: "internal" } });
    expect(screen.getByText("Internal notes")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
    expect(screen.queryByText("Internal notes")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Request customer approval" }));
    await waitFor(() => expect(api.requestApproval).toHaveBeenCalledWith(packageRecord.id));
    expect(await screen.findByText(/No external notification was sent/)).toBeDefined();
  });
});
