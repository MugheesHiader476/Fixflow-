import { beforeEach, describe, expect, it, vi } from "vitest";
import * as proof from "@/lib/proofos";
import { approvalRecord, criterion, milestoneId, packageRecord, projectId, proofWorkspace, requirementId, sourceId } from "./fixtures/proofos";

const request = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiFetch: request }));

beforeEach(() => { request.mockReset().mockResolvedValue({}); });

describe("ProofOS authenticated gateway client", () => {
  const project = { name: "Website", customer_name: "Fictional customer", description: "Reviewed scope", customer_approver_id: null, github_account_id: null, repository_id: null, repository_name: null, branch: "main" };
  const current = proofWorkspace().milestones[0];
  const milestone = { contract_id: current.contract_id, code: current.code, title: current.title, description: current.description, expected_sha: current.expected_sha, amount_minor: current.amount_minor, currency: current.currency, payment_terms: current.payment_terms };
  const review = { interpretation: "Reviewed wording", acceptance_criteria: [criterion], ambiguities: [], engineering_links: [], approve: false };
  const operations: { name: string; invoke: () => Promise<unknown>; path: string; body: object }[] = [
    { name: "create project", invoke: () => proof.createProject(project), path: "projects", body: project },
    { name: "extract contract", invoke: () => proof.extractContract(projectId, sourceId, "Agreement"), path: `projects/${projectId}/contracts`, body: { source_id: sourceId, title: "Agreement" } },
    { name: "approve contract", invoke: () => proof.approveContract("contract"), path: "contracts/contract/approve", body: {} },
    { name: "review requirement", invoke: () => proof.reviewRequirement(requirementId, review), path: `requirements/${requirementId}/review`, body: review },
    { name: "assign requirement", invoke: () => proof.assignRequirement(requirementId, milestoneId), path: `requirements/${requirementId}/assign`, body: { milestone_id: milestoneId } },
    { name: "create milestone", invoke: () => proof.createMilestone(projectId, milestone), path: `projects/${projectId}/milestones`, body: milestone },
    { name: "approve milestone", invoke: () => proof.approveMilestone(milestoneId), path: `milestones/${milestoneId}/approve`, body: {} },
    { name: "collect native evidence", invoke: () => proof.collectEvidence(milestoneId, { run_id: 18, workflow_id: 7, artifact_names: ["junit"] }), path: `milestones/${milestoneId}/collect`, body: { run_id: 18, workflow_id: 7, artifact_names: ["junit"] } },
    { name: "attach source", invoke: () => proof.addMilestoneEvidence(milestoneId, { source_id: sourceId, requirement_ids: [requirementId], kind: "document", customer_safe: false }), path: `milestones/${milestoneId}/evidence`, body: { source_id: sourceId, requirement_ids: [requirementId], kind: "document", customer_safe: false } },
    { name: "verify", invoke: () => proof.verifyMilestone(milestoneId), path: `milestones/${milestoneId}/verify`, body: {} },
    { name: "prepare package", invoke: () => proof.preparePackage(milestoneId), path: `milestones/${milestoneId}/packages`, body: {} },
    { name: "review package", invoke: () => proof.reviewPackage(packageRecord.id, packageRecord.internal_hash, "rejected", "Missing evidence"), path: `packages/${packageRecord.id}/review`, body: { expected_hash: packageRecord.internal_hash, decision: "rejected", reason: "Missing evidence" } },
    { name: "request customer review", invoke: () => proof.requestApproval(packageRecord.id), path: `packages/${packageRecord.id}/request-approval`, body: { expires_in_hours: 72 } },
    { name: "customer decision", invoke: () => proof.decideApproval(approvalRecord.id, approvalRecord.package_hash, "rework_requested", "Correct delivery"), path: `approvals/${approvalRecord.id}/decide`, body: { expected_hash: approvalRecord.package_hash, decision: "rework_requested", reason: "Correct delivery" } },
    { name: "draft invoice", invoke: () => proof.prepareInvoice(milestoneId), path: `milestones/${milestoneId}/invoice-draft`, body: {} },
    { name: "cancel job", invoke: () => proof.cancelProofJob("job"), path: "jobs/job/cancel", body: {} },
  ];
  it.each(operations)("serializes $name without inventing authority", async ({ invoke, path, body }) => {
    await invoke();
    expect(request).toHaveBeenCalledExactlyOnceWith(`/api/proofos/${path}`, { method: "POST", body: JSON.stringify(body), signal: undefined });
  });
  it("passes cancellation through every read and project creation", async () => {
    const signal = new AbortController().signal;
    const reads = [
      { invoke: () => proof.listProjects(signal), path: "projects" },
      { invoke: () => proof.projectWorkspace(projectId, signal), path: `projects/${projectId}` },
      { invoke: () => proof.packageDetail(packageRecord.id, signal), path: `packages/${packageRecord.id}` },
      { invoke: () => proof.listApprovals(signal), path: "approvals" },
      { invoke: () => proof.approvalDetail(approvalRecord.id, signal), path: `approvals/${approvalRecord.id}` },
      { invoke: () => proof.proofJob("job", signal), path: "jobs/job" },
      { invoke: () => proof.taskPacket(milestoneId, signal), path: `milestones/${milestoneId}/task-packet` },
    ];
    for (const operation of reads) {
      await operation.invoke();
      expect(request).toHaveBeenLastCalledWith(`/api/proofos/${operation.path}`, { signal });
    }
    await proof.createProject(project, signal);
    expect(request).toHaveBeenLastCalledWith("/api/proofos/projects", { method: "POST", body: JSON.stringify(project), signal });
  });
  it("encodes identifiers in API paths and private download URLs", async () => {
    const unsafe = "folder/name?admin=true";
    await proof.projectWorkspace(unsafe);
    expect(request).toHaveBeenLastCalledWith("/api/proofos/projects/folder%2Fname%3Fadmin%3Dtrue", { signal: undefined });
    expect(proof.packageDownload(unsafe, "customer", "pdf")).toBe("/api/backend/api/proofos/packages/folder%2Fname%3Fadmin%3Dtrue/download/customer/pdf");
    expect(proof.approvalDownload(unsafe, "json")).toBe("/api/backend/api/proofos/approvals/folder%2Fname%3Fadmin%3Dtrue/download/json");
  });
  it("propagates gateway rejection rather than manufacturing a receipt", async () => {
    request.mockRejectedValue(new Error("Access revoked"));
    await expect(proof.prepareInvoice(milestoneId)).rejects.toThrow("Access revoked");
  });
});
