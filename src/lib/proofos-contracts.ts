export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };
export type VerificationStatus = "PASSED" | "FAILED" | "UNVERIFIED" | "BLOCKED" | "NOT_APPLICABLE";
export type CriterionCategory = "technical" | "delivery" | "customer";
export type CriterionKind = "ci" | "deployment" | "document" | "review" | "discussion" | "manual";
export interface Criterion {
  id: string;
  description: string;
  category: CriterionCategory;
  kind: CriterionKind;
  mandatory: boolean;
  repository_id: string | null;
  branch: string | null;
  sha: string | null;
  workflow_id: number | null;
  run_id: number | null;
  suite: string | null;
  environment?: string | null;
  cases: string[];
  max_age_hours: number;
  allow_skipped: boolean;
}
export interface EngineeringLink {
  kind: "issue" | "pull_request" | "commit" | "release" | "task";
  external_id: string;
  url: string | null;
  title: string;
}
export interface ProjectCreate {
  name: string;
  customer_name: string;
  description: string;
  customer_approver_id: string | null;
  github_account_id: string | null;
  repository_id: string | null;
  repository_name: string | null;
  branch: string;
}
export interface ProofProject extends ProjectCreate { id: string; created_at: string }
export interface ContractVersion {
  id: string;
  project_id: string;
  title: string;
  source_id: string;
  source_hash: string;
  source_version: number;
  revision: number;
  state: "DRAFT" | "APPROVED" | "SUPERSEDED";
  created_at: string;
}
export interface Requirement {
  id: string;
  contract_id: string;
  milestone_id: string | null;
  stable_id: string;
  original_text: string;
  interpretation: string;
  acceptance_criteria: Criterion[];
  source_location: JsonObject;
  ambiguities: string[];
  state: "NEEDS_REVIEW" | "APPROVED";
  engineering_links: EngineeringLink[];
  created_at: string;
}
export interface VerificationCheck {
  criterion_id: string;
  category: CriterionCategory;
  status: VerificationStatus;
  explanation: string;
  evidence_ids: string[];
}
export interface Verification {
  id: string;
  milestone_id: string;
  input_hash: string;
  result: {
    policy_version: string;
    technical_status: VerificationStatus;
    delivery_status: VerificationStatus;
    checks: VerificationCheck[];
    verified_at: string;
    input_hash: string;
  };
  created_at: string;
}
export interface MilestoneCreate {
  contract_id: string;
  code: string;
  title: string;
  description: string;
  expected_sha: string | null;
  amount_minor: number | null;
  currency: string | null;
  payment_terms: string | null;
}
export interface Milestone extends MilestoneCreate {
  id: string;
  project_id: string;
  scope_approved: boolean;
  created_at: string;
  readiness: { eligible: boolean; reasons: string[] };
  latest_verification: Verification | null;
}
export interface EvidenceFact {
  id: string;
  kind: string;
  provider: string;
  repository_id: string | null;
  branch: string | null;
  workflow_id: number | null;
  external_id: string | null;
  environment?: string | null;
  title: string;
  details: string;
  status: string;
  sha: string | null;
  run_id: number | null;
  suite: string | null;
  cases: string[];
  url: string | null;
  source_id: string | null;
  source_hash: string | null;
  observed_at: string;
  event_at: string | null;
  verified_native: boolean;
  complete: boolean;
  customer_safe: boolean;
  requirement_ids: string[];
}
export interface ProofEvidence { id: string; milestone_id: string; fact: EvidenceFact; created_at: string; current?: boolean }
export interface AcceptancePackage {
  id: string;
  milestone_id: string;
  verification_id: string;
  version: number;
  customer_hash: string;
  internal_hash: string;
  internal_review: "pending" | "approved" | "rejected";
  customer_status: "pending" | "accepted" | "rejected" | "rework_requested";
  created_at: string;
}
export interface ApprovalRequest {
  id: string;
  project_id: string;
  package_id: string;
  package_hash: string;
  customer_subject: string;
  expires_at: string;
  status: "pending" | "accepted" | "rejected" | "rework_requested";
  decided_at: string | null;
  title?: string;
  customer_name?: string;
}
export interface InvoiceDraft {
  id: string;
  milestone_id: string;
  package_id: string;
  amount_minor: number;
  currency: string;
  payment_terms: string;
  status: "draft";
  created_at: string;
}
export interface ProofJob {
  id: string;
  project_id: string;
  kind: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  error: string | null;
  result: JsonObject;
  created_at: string;
}
export interface ProjectWorkspace {
  coverage?: string;
  project: ProofProject;
  contracts: ContractVersion[];
  requirements: Requirement[];
  milestones: Milestone[];
  evidence: ProofEvidence[];
  verifications: Verification[];
  packages: AcceptancePackage[];
  approvals: ApprovalRequest[];
  invoices: InvoiceDraft[];
  jobs: ProofJob[];
  audit: JsonObject[];
}
export interface PackageDetail { package: AcceptancePackage; internal: JsonObject; customer: JsonObject }
export interface ApprovalDetail { approval: ApprovalRequest; package: JsonObject }

const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string";
const optionalText = (value: unknown) => value === null || text(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const optionalInteger = (value: unknown) => value === null || integer(value);
const strings = (value: unknown) => Array.isArray(value) && value.every(text);
const fields = (value: Record<string, unknown>, names: string[]) => names.every((name) => text(value[name]));
const list = (value: unknown, check: (item: unknown) => boolean) => Array.isArray(value) && value.every(check);
const member = (value: unknown, options: readonly string[]) => text(value) && options.includes(value);
const statuses = ["PASSED", "FAILED", "UNVERIFIED", "BLOCKED", "NOT_APPLICABLE"];
const categories = ["technical", "delivery", "customer"];
const decisions = ["pending", "accepted", "rejected", "rework_requested"];

export function validCriterion(value: unknown): value is Criterion {
  return record(value) && fields(value, ["id", "description"]) && member(value.category, categories)
    && member(value.kind, ["ci", "deployment", "document", "review", "discussion", "manual"])
    && ["mandatory", "allow_skipped"].every((name) => typeof value[name] === "boolean")
    && ["repository_id", "branch", "sha", "suite"].every((name) => optionalText(value[name]))
    && optionalInteger(value.workflow_id) && optionalInteger(value.run_id) && strings(value.cases)
    && (value.environment === undefined || optionalText(value.environment))
    && typeof value.max_age_hours === "number" && Number.isFinite(value.max_age_hours) && value.max_age_hours > 0;
}
function validEngineeringLink(value: unknown): boolean {
  return record(value) && fields(value, ["external_id", "title"]) && optionalText(value.url)
    && member(value.kind, ["issue", "pull_request", "commit", "release", "task"]);
}
function validProject(value: unknown): boolean {
  return record(value) && fields(value, ["id", "name", "customer_name", "description", "branch", "created_at"])
    && ["customer_approver_id", "github_account_id", "repository_id", "repository_name"].every((name) => optionalText(value[name]));
}
function validContract(value: unknown): boolean {
  return record(value) && fields(value, ["id", "project_id", "title", "source_id", "source_hash", "created_at"])
    && integer(value.source_version) && integer(value.revision) && member(value.state, ["DRAFT", "APPROVED", "SUPERSEDED"]);
}
function validRequirement(value: unknown): boolean {
  return record(value) && fields(value, ["id", "contract_id", "stable_id", "original_text", "interpretation", "created_at"])
    && optionalText(value.milestone_id) && record(value.source_location) && strings(value.ambiguities)
    && member(value.state, ["NEEDS_REVIEW", "APPROVED"]) && list(value.acceptance_criteria, validCriterion)
    && list(value.engineering_links, validEngineeringLink);
}
function validCheck(value: unknown): boolean {
  return record(value) && fields(value, ["criterion_id", "explanation"]) && member(value.category, categories)
    && member(value.status, statuses) && strings(value.evidence_ids);
}
function validVerification(value: unknown): boolean {
  if (!record(value) || !fields(value, ["id", "milestone_id", "input_hash", "created_at"]) || !record(value.result)) return false;
  const result = value.result;
  return fields(result, ["policy_version", "verified_at", "input_hash"]) && member(result.technical_status, statuses)
    && member(result.delivery_status, statuses) && list(result.checks, validCheck);
}
function validMilestone(value: unknown): boolean {
  return record(value) && fields(value, ["id", "project_id", "contract_id", "code", "title", "description", "created_at"])
    && ["expected_sha", "currency", "payment_terms"].every((name) => optionalText(value[name]))
    && optionalInteger(value.amount_minor) && typeof value.scope_approved === "boolean"
    && record(value.readiness) && typeof value.readiness.eligible === "boolean" && strings(value.readiness.reasons)
    && (value.latest_verification === null || validVerification(value.latest_verification));
}
function validEvidence(value: unknown): boolean {
  if (!record(value) || !fields(value, ["id", "milestone_id", "created_at"]) || !record(value.fact)) return false;
  const fact = value.fact;
  return fields(fact, ["id", "kind", "provider", "title", "details", "observed_at"])
    && member(fact.status, ["passed", "failed", "pending", "unknown", "skipped"])
    && ["repository_id", "branch", "external_id", "sha", "suite", "url", "source_id", "source_hash", "event_at"].every((name) => optionalText(fact[name]))
    && optionalInteger(fact.run_id) && optionalInteger(fact.workflow_id) && strings(fact.cases) && strings(fact.requirement_ids)
    && (value.current === undefined || typeof value.current === "boolean")
    && (fact.environment === undefined || optionalText(fact.environment))
    && ["verified_native", "complete", "customer_safe"].every((name) => typeof fact[name] === "boolean");
}
function validPackage(value: unknown): boolean {
  return record(value) && fields(value, ["id", "milestone_id", "verification_id", "customer_hash", "internal_hash", "created_at"])
    && integer(value.version) && member(value.internal_review, ["pending", "approved", "rejected"])
    && member(value.customer_status, decisions);
}
function validApproval(value: unknown): boolean {
  return record(value) && fields(value, ["id", "project_id", "package_id", "package_hash", "customer_subject", "expires_at"])
    && member(value.status, decisions) && optionalText(value.decided_at)
    && ["title", "customer_name"].every((name) => value[name] === undefined || text(value[name]));
}
function validInvoice(value: unknown): boolean {
  return record(value) && fields(value, ["id", "milestone_id", "package_id", "currency", "payment_terms", "created_at"])
    && integer(value.amount_minor) && value.status === "draft";
}
function validJob(value: unknown): boolean {
  return record(value) && fields(value, ["id", "project_id", "kind", "created_at"]) && optionalText(value.error) && record(value.result)
    && member(value.status, ["queued", "running", "completed", "failed", "cancelled"]);
}
function validWorkspace(value: unknown): boolean {
  return record(value) && validProject(value.project) && list(value.contracts, validContract)
    && (value.coverage === undefined || text(value.coverage))
    && list(value.requirements, validRequirement) && list(value.milestones, validMilestone)
    && list(value.evidence, validEvidence) && list(value.verifications, validVerification)
    && list(value.packages, validPackage) && list(value.approvals, validApproval)
    && list(value.invoices, validInvoice) && list(value.jobs, validJob) && list(value.audit, record);
}
const routeChecks: [RegExp, (value: unknown) => boolean][] = [
  [/^projects\/[^/]+$/, validWorkspace],
  [/^(jobs\/[^/]+(?:\/cancel)?|projects\/[^/]+\/contracts|milestones\/[^/]+\/(collect|evidence|verify|packages))$/, validJob],
  [/^(projects\/[^/]+\/milestones|milestones\/[^/]+\/approve)$/, validMilestone],
  [/^contracts\/[^/]+\/approve$/, validContract],
  [/^requirements\/[^/]+\/(review|assign)$/, validRequirement],
  [/^packages\/[^/]+$/, (value) => record(value) && validPackage(value.package) && record(value.internal) && record(value.customer)],
  [/^packages\/[^/]+\/review$/, validPackage],
  [/^(packages\/[^/]+\/request-approval|approvals\/[^/]+\/decide)$/, validApproval],
  [/^approvals$/, (value) => list(value, validApproval)],
  [/^approvals\/[^/]+$/, (value) => record(value) && validApproval(value.approval) && record(value.package)],
  [/^milestones\/[^/]+\/invoice-draft$/, validInvoice],
  [/^milestones\/[^/]+\/task-packet$/, record],
];
export function validProofResponse(path: string, value: unknown, method = "GET"): boolean {
  const route = path.replace(/^\/api\/proofos\//, "");
  if (route === "projects") return method === "POST" ? validProject(value) : list(value, validProject);
  return routeChecks.find(([pattern]) => pattern.test(route))?.[1](value) ?? false;
}
