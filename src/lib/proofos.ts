import { apiFetch } from "./api";
import type { AcceptancePackage, ApprovalDetail, ApprovalRequest, ContractVersion, Criterion, EngineeringLink, InvoiceDraft, JsonObject, Milestone, MilestoneCreate, PackageDetail, ProjectCreate, ProjectWorkspace, ProofJob, ProofProject, Requirement } from "./proofos-contracts";

const base = "/api/proofos";
const identifier = encodeURIComponent;
function post<T>(path: string, body: object = {}, signal?: AbortSignal): Promise<T> {
  return apiFetch(`${base}/${path}`, { method: "POST", body: JSON.stringify(body), signal });
}
export function listProjects(signal?: AbortSignal): Promise<ProofProject[]> { return apiFetch(`${base}/projects`, { signal }); }
export function createProject(body: ProjectCreate, signal?: AbortSignal): Promise<ProofProject> { return post("projects", body, signal); }
export function projectWorkspace(id: string, signal?: AbortSignal): Promise<ProjectWorkspace> { return apiFetch(`${base}/projects/${identifier(id)}`, { signal }); }
export function extractContract(projectId: string, sourceId: string, title: string): Promise<ProofJob> { return post(`projects/${identifier(projectId)}/contracts`, { source_id: sourceId, title }); }
export function approveContract(id: string): Promise<ContractVersion> { return post(`contracts/${identifier(id)}/approve`); }
export function reviewRequirement(id: string, body: { interpretation: string; acceptance_criteria: Criterion[]; ambiguities: string[]; engineering_links: EngineeringLink[]; approve: boolean }): Promise<Requirement> { return post(`requirements/${identifier(id)}/review`, body); }
export function assignRequirement(id: string, milestoneId: string): Promise<Requirement> { return post(`requirements/${identifier(id)}/assign`, { milestone_id: milestoneId }); }
export function createMilestone(projectId: string, body: MilestoneCreate): Promise<Milestone> { return post(`projects/${identifier(projectId)}/milestones`, body); }
export function approveMilestone(id: string): Promise<Milestone> { return post(`milestones/${identifier(id)}/approve`); }
export function collectEvidence(id: string, body: { run_id: number; workflow_id: number; artifact_names: string[] }): Promise<ProofJob> { return post(`milestones/${identifier(id)}/collect`, body); }
export function addMilestoneEvidence(id: string, body: { source_id: string; requirement_ids: string[]; kind: "document" | "discussion"; customer_safe: boolean }): Promise<ProofJob> { return post(`milestones/${identifier(id)}/evidence`, body); }
export function verifyMilestone(id: string): Promise<ProofJob> { return post(`milestones/${identifier(id)}/verify`); }
export function preparePackage(id: string): Promise<ProofJob> { return post(`milestones/${identifier(id)}/packages`); }
export function packageDetail(id: string, signal?: AbortSignal): Promise<PackageDetail> { return apiFetch(`${base}/packages/${identifier(id)}`, { signal }); }
export function reviewPackage(id: string, expectedHash: string, decision: "approved" | "rejected", reason: string): Promise<AcceptancePackage> { return post(`packages/${identifier(id)}/review`, { expected_hash: expectedHash, decision, reason }); }
export function requestApproval(id: string): Promise<ApprovalRequest> { return post(`packages/${identifier(id)}/request-approval`, { expires_in_hours: 72 }); }
export function listApprovals(signal?: AbortSignal): Promise<ApprovalRequest[]> { return apiFetch(`${base}/approvals`, { signal }); }
export function approvalDetail(id: string, signal?: AbortSignal): Promise<ApprovalDetail> { return apiFetch(`${base}/approvals/${identifier(id)}`, { signal }); }
export function decideApproval(id: string, expectedHash: string, decision: "accepted" | "rejected" | "rework_requested", reason: string): Promise<ApprovalRequest> { return post(`approvals/${identifier(id)}/decide`, { expected_hash: expectedHash, decision, reason }); }
export function prepareInvoice(id: string): Promise<InvoiceDraft> { return post(`milestones/${identifier(id)}/invoice-draft`); }
export function proofJob(id: string, signal?: AbortSignal): Promise<ProofJob> { return apiFetch(`${base}/jobs/${identifier(id)}`, { signal }); }
export function cancelProofJob(id: string): Promise<ProofJob> { return post(`jobs/${identifier(id)}/cancel`); }
export function taskPacket(id: string, signal?: AbortSignal): Promise<JsonObject> { return apiFetch(`${base}/milestones/${identifier(id)}/task-packet`, { signal }); }
export function packageDownload(id: string, audience: "internal" | "customer", format: "pdf" | "json"): string { return `/api/backend${base}/packages/${identifier(id)}/download/${audience}/${format}`; }
export function approvalDownload(id: string, format: "pdf" | "json"): string { return `/api/backend${base}/approvals/${identifier(id)}/download/${format}`; }
