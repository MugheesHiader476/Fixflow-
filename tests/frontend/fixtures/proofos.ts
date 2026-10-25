import type { AcceptancePackage, ApprovalRequest, Criterion, ProjectWorkspace } from "@/lib/proofos-contracts";

export const projectId = "11111111-1111-4111-8111-111111111111";
export const contractId = "22222222-2222-4222-8222-222222222222";
export const milestoneId = "33333333-3333-4333-8333-333333333333";
export const requirementId = "44444444-4444-4444-8444-444444444444";
export const verificationId = "55555555-5555-4555-8555-555555555555";
export const packageId = "66666666-6666-4666-8666-666666666666";
export const sourceId = "77777777-7777-4777-8777-777777777777";
export const criterion: Criterion = { id: "REQ-001-auth-tests", description: "Authentication mandatory tests pass", category: "technical", kind: "ci", mandatory: true, repository_id: "42", branch: "main", sha: "a".repeat(40), workflow_id: 7, run_id: 18, suite: "authentication", cases: ["valid login"], max_age_hours: 168, allow_skipped: false };
export const packageRecord: AcceptancePackage = { id: packageId, milestone_id: milestoneId, verification_id: verificationId, version: 1, customer_hash: "c".repeat(64), internal_hash: "d".repeat(64), internal_review: "pending", customer_status: "pending", created_at: "2026-10-25T00:00:00Z" };
export const approvalRecord: ApprovalRequest = { id: "88888888-8888-4888-8888-888888888888", project_id: projectId, package_id: packageId, package_hash: "c".repeat(64), customer_subject: "user_customer", expires_at: "2099-10-28T00:00:00Z", status: "pending", decided_at: null, title: "Customer Website", customer_name: "Fictional Customer" };
export function proofWorkspace(): ProjectWorkspace {
  const verification = { id: verificationId, milestone_id: milestoneId, input_hash: "b".repeat(64), created_at: "2026-10-25T00:00:00Z", result: { policy_version: "1", technical_status: "FAILED" as const, delivery_status: "UNVERIFIED" as const, verified_at: "2026-10-25T00:00:00Z", input_hash: "b".repeat(64), checks: [
    { criterion_id: criterion.id, category: "technical" as const, status: "FAILED" as const, explanation: "Mandatory authentication test failed for the applicable SHA.", evidence_ids: ["ci-18"] },
    { criterion_id: "delivery-docs", category: "delivery" as const, status: "UNVERIFIED" as const, explanation: "Delivery documentation is missing.", evidence_ids: [] },
  ] } };
  return {
    project: { id: projectId, name: "Customer Website", customer_name: "Fictional Customer", description: "Fictional acceptance fixture", customer_approver_id: "user_customer", github_account_id: null, repository_id: null, repository_name: null, branch: "main", created_at: "2026-10-25T00:00:00Z" },
    contracts: [{ id: contractId, project_id: projectId, title: "Website SRS", source_id: sourceId, source_hash: "e".repeat(64), source_version: 1, revision: 1, state: "DRAFT", created_at: "2026-10-25T00:00:00Z" }],
    requirements: [{ id: requirementId, contract_id: contractId, milestone_id: milestoneId, stable_id: "REQ-001", original_text: "Authentication must pass all mandatory tests.", interpretation: "Verify the authentication test suite against the applicable commit.", acceptance_criteria: [criterion], source_location: { page: 1, block_id: "block-1" }, ambiguities: ["Confirm required test case names"], state: "NEEDS_REVIEW", engineering_links: [], created_at: "2026-10-25T00:00:00Z" }],
    milestones: [{ id: milestoneId, project_id: projectId, contract_id: contractId, code: "M-02", title: "Website delivery", description: "Authentication, homepage and contact form", expected_sha: "a".repeat(40), amount_minor: 1000000, currency: "USD", payment_terms: "Net 30 after acceptance", scope_approved: true, created_at: "2026-10-25T00:00:00Z", readiness: { eligible: false, reasons: ["Mandatory tests failed", "Delivery documentation is missing", "Customer approval is pending"] }, latest_verification: verification }],
    evidence: [{ id: "99999999-9999-4999-8999-999999999999", milestone_id: milestoneId, current: true, created_at: "2026-10-25T00:00:00Z", fact: { id: "ci-18", kind: "ci", provider: "github", title: "Authentication tests", details: "valid login failed", status: "failed", repository_id: "42", branch: "main", workflow_id: 7, external_id: "18", sha: "a".repeat(40), run_id: 18, suite: "authentication", cases: [], url: "https://github.com/fictional/customer/actions/runs/18", source_id: null, source_hash: null, observed_at: "2026-10-25T00:00:00Z", event_at: "2026-10-25T00:00:00Z", verified_native: true, complete: true, customer_safe: true, requirement_ids: [criterion.id] } }],
    verifications: [verification], packages: [], approvals: [], invoices: [], jobs: [], audit: [],
  };
}
