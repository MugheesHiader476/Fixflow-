import type { WorkflowMaterial } from "./agent";

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function count(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}
function optionalText(value: unknown): boolean { return value === null || typeof value === "string"; }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.every((entry) => typeof entry === "string"); }
function payload(value: unknown): boolean {
  return record(value) && ["recipient", "subject", "body"].every((key) => value[key] === undefined || typeof value[key] === "string");
}
function action(value: unknown): boolean {
  return record(value) && typeof value.id === "string" && typeof value.account_id === "string"
    && ["draft", "dispatching", "sent", "blocked", "unknown"].includes(String(value.status))
    && payload(value.payload) && typeof value.payload_hash === "string" && /^[a-f0-9]{64}$/.test(value.payload_hash)
    && optionalText(value.error) && optionalText(value.provider_message_id);
}
function material(value: unknown): value is WorkflowMaterial {
  return record(value) && typeof value.id === "string" && /^e-[a-f0-9]{32}$/.test(value.id)
    && typeof value.title === "string" && typeof value.text === "string" && value.text.length <= 12000
    && record(value.reference) && typeof value.observed_at === "string" && optionalText(value.warning);
}
function file(value: unknown): boolean {
  return record(value) && typeof value.id === "string" && typeof value.filename === "string"
    && typeof value.mime_type === "string" && typeof value.download_path === "string"
    && /^\/api\/agent\/files\/[a-f0-9-]{36}\/download$/.test(value.download_path);
}
function validAnswer(value: unknown, materials: WorkflowMaterial[]): boolean {
  if (value === null) return true;
  if (!record(value) || typeof value.answer !== "string" || !strings(value.limitations)
    || !Array.isArray(value.claims) || value.claims.length > 12) return false;
  const known = new Map(materials.map((entry) => [entry.id, entry.text]));
  return value.claims.every((claim: unknown) => record(claim) && typeof claim.text === "string"
    && Array.isArray(claim.citations) && claim.citations.length > 0
    && claim.citations.every((cite: unknown) => record(cite) && typeof cite.evidence_id === "string"
      && typeof cite.quote === "string" && cite.quote.trim().length > 0 && known.get(cite.evidence_id)?.includes(cite.quote)));
}
function run(value: unknown): boolean {
  if (!record(value) || typeof value.id !== "string" || typeof value.question !== "string" || !count(value.steps)
    || !["queued", "running", "completed", "failed", "cancelled"].includes(String(value.status))
    || typeof value.created_at !== "string" || !optionalText(value.error) || !record(value.usage)
    || !Object.values(value.usage).every((entry) => entry === null || count(entry))) return false;
  if (!Array.isArray(value.materials) || value.materials.length > 32 || !value.materials.every(material)
    || !Array.isArray(value.actions) || !value.actions.every(action)
    || !Array.isArray(value.files) || !value.files.every(file)) return false;
  return validAnswer(value.answer, value.materials);
}
export function validWorkflowResponse(path: string, value: unknown): boolean {
  if (path.includes("/actions/")) return action(value);
  return path === "/api/agent/runs" && Array.isArray(value) ? value.every(run) : run(value);
}
