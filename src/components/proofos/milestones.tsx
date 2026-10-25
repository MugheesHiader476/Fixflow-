"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { approveMilestone, createMilestone, prepareInvoice, preparePackage, taskPacket, verifyMilestone } from "@/lib/proofos";
import type { JsonObject, Milestone, MilestoneCreate, ProjectWorkspace, Verification } from "@/lib/proofos-contracts";
import { ActionFeedback, EmptyState, formatDate, inputClass, money, ProofSection, StatusBadge, useProofAction } from "./shared";

export function downloadJson(value: JsonObject, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
}

export function VerificationDetails({ verification }: { verification: Verification }) {
  return <div className="proof-verification"><div className="proof-actions"><span>Technical</span><StatusBadge status={verification.result.technical_status} /><span>Delivery</span><StatusBadge status={verification.result.delivery_status} /></div>
    <p className="proof-description">Verified {formatDate(verification.result.verified_at)} · policy {verification.result.policy_version}</p>
    <div className="proof-checks">{verification.result.checks.map((check) => <div key={check.criterion_id} className="proof-check"><div className="proof-actions"><strong>{check.criterion_id}</strong><StatusBadge status={check.status} /><span className="text-xs text-muted">{check.category}</span></div><p>{check.explanation}</p>{!!check.evidence_ids.length && <p className="text-xs text-muted">Evidence: {check.evidence_ids.join(", ")}</p>}</div>)}</div>
    <details className="mt-3 text-xs text-muted"><summary>Verification provenance</summary><p className="mt-2 break-all">Execution {verification.id}</p><p className="mt-2 break-all">Input SHA-256 {verification.input_hash}</p></details>
  </div>;
}

function MilestoneCard({ milestone, data, refresh }: { milestone: Milestone; data: ProjectWorkspace; refresh: () => void }) {
  const action = useProofAction(refresh);
  const requirements = data.requirements.filter((item) => item.milestone_id === milestone.id);
  const invoice = data.invoices.find((item) => item.milestone_id === milestone.id);
  const packages = data.packages.filter((item) => item.milestone_id === milestone.id);
  const latestPackage = [...packages].sort((left, right) => right.version - left.version)[0];
  const packageDescription = latestPackage ? `${packages.length} package versions. Latest recorded customer state: ${latestPackage.customer_status.replaceAll("_", " ")}.` : "No acceptance package generated.";
  return <article className="proof-milestone"><div className="proof-list-row"><div><p className="font-mono text-xs text-muted">{milestone.code}</p><h3>{milestone.title}</h3></div><div className="proof-actions"><span className="text-xs text-muted">Scope</span><StatusBadge status={milestone.scope_approved ? "APPROVED" : "DRAFT"} /></div></div>
    <p className="proof-description">{milestone.description || "No milestone description supplied."}</p>
    <dl className="proof-facts"><div><dt>Approved requirements</dt><dd>{requirements.filter((item) => item.state === "APPROVED").length} of {requirements.length} assigned</dd></div><div><dt>Applicable commit</dt><dd className="font-mono">{milestone.expected_sha ?? "Not configured"}</dd></div><div><dt>Contract value</dt><dd>{milestone.amount_minor !== null && milestone.currency ? money(milestone.amount_minor, milestone.currency) : "Not configured"}</dd></div><div><dt>Payment terms</dt><dd>{milestone.payment_terms ?? "Not configured"}</dd></div></dl>
    {requirements.length > 0 && <div className="proof-actions my-3">{requirements.map((item) => <span key={item.id} className="rounded border border-border px-2 py-1 font-mono text-xs">{item.stable_id}</span>)}</div>}
    <div className="proof-actions my-4">
      {!milestone.scope_approved && <Button disabled={action.busy || !requirements.length} onClick={() => void action.perform(() => approveMilestone(milestone.id), "Milestone scope approved and frozen.")}>Approve & freeze scope</Button>}
      <Button variant="primary" disabled={action.busy || !milestone.scope_approved} onClick={() => void action.perform(() => verifyMilestone(milestone.id), "Verification queued. Its durable execution appears in Activity.")}>Verify milestone</Button>
      <Button disabled={action.busy || !milestone.latest_verification} onClick={() => void action.perform(() => preparePackage(milestone.id), "Acceptance package generation queued.")}>Prepare acceptance package</Button>
      <Button disabled={action.busy || !milestone.scope_approved} onClick={() => void action.perform(async () => { downloadJson(await taskPacket(milestone.id), `${milestone.code}-engineering-task.json`); }, "Engineering task packet downloaded. Execution requires a separately authorized isolated runner.")}>Download engineering task packet</Button>
    </div>
    <p className="proof-description">Scope approval freezes the requirements and applicable commit. Verification executes deterministic checks against collected evidence.</p>
    {milestone.latest_verification ? <VerificationDetails verification={milestone.latest_verification} /> : <EmptyState>No verification execution yet. Collect evidence, then verify this milestone.</EmptyState>}
    <div className="proof-readiness"><h4>Customer acceptance & invoice readiness</h4><p>{packageDescription}</p>
      <p className={milestone.readiness.eligible ? "text-success" : "text-warning"}>{milestone.readiness.eligible ? "Invoice prerequisites satisfied." : "Invoice draft blocked."}</p>
      {milestone.readiness.reasons.length > 0 && <ul>{milestone.readiness.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
      {invoice ? <p className="mt-3 text-sm">Invoice draft: {money(invoice.amount_minor, invoice.currency)} · {invoice.payment_terms} · {formatDate(invoice.created_at)}. No invoice has been sent.</p> : <Button className="mt-3" disabled={action.busy || !milestone.readiness.eligible} onClick={() => void action.perform(() => prepareInvoice(milestone.id), "Invoice draft prepared. Sending and payment processing are unavailable.")}>Prepare invoice draft</Button>}
    </div>
    <ActionFeedback action={action} />
  </article>;
}

export function Milestones({ data, refresh }: { data: ProjectWorkspace; refresh: () => void }) {
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<MilestoneCreate>({ contract_id: "", code: "", title: "", description: "", expected_sha: null, amount_minor: null, currency: null, payment_terms: null });
  const action = useProofAction(refresh);
  function submit(event: FormEvent) {
    event.preventDefault();
    void action.perform(() => createMilestone(data.project.id, form), "Milestone created. Assign reviewed requirements before approving its scope.").then((success) => { if (success) setShowForm(false); });
  }
  return <ProofSection title="Milestone workspace" description="A passing technical check never substitutes for delivery evidence or authorized customer acceptance.">
    <Button onClick={() => setShowForm(!showForm)}>Add milestone</Button>
    {showForm && <form onSubmit={submit} className="proof-form mt-5"><fieldset disabled={action.busy} className="proof-form-grid">
      <label>Contract revision<select required className={inputClass} value={form.contract_id} onChange={(event) => setForm({ ...form, contract_id: event.target.value })}><option value="">Choose a contract revision</option>{data.contracts.filter((contract) => contract.state !== "SUPERSEDED").map((contract) => <option key={contract.id} value={contract.id}>{contract.title} · revision {contract.revision} · {contract.state}</option>)}</select></label>
      <label>Milestone code<input required maxLength={40} pattern="[A-Za-z0-9_.-]+" placeholder="M-02" className={inputClass} value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value })} /></label>
      <label className="proof-wide">Milestone title<input required maxLength={200} className={inputClass} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
      <label className="proof-wide">Description<textarea rows={3} maxLength={4000} className={inputClass} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      <label className="proof-wide">Applicable commit SHA<input pattern="[a-f0-9]{40}" maxLength={40} className={`${inputClass} font-mono`} value={form.expected_sha ?? ""} onChange={(event) => setForm({ ...form, expected_sha: event.target.value || null })} /></label>
      <label>Contract amount in minor units (optional)<input type="number" min={1} max={1000000000} step={1} className={inputClass} placeholder="1000000 = USD 10,000" value={form.amount_minor ?? ""} onChange={(event) => setForm({ ...form, amount_minor: event.target.value ? Number(event.target.value) : null })} /></label>
      <label>Currency (optional)<input pattern="[A-Z]{3}" maxLength={3} placeholder="USD" className={inputClass} value={form.currency ?? ""} onChange={(event) => setForm({ ...form, currency: event.target.value.toUpperCase() || null })} /></label>
      <label className="proof-wide">Contractual payment terms (optional)<textarea maxLength={2000} rows={2} className={inputClass} value={form.payment_terms ?? ""} onChange={(event) => setForm({ ...form, payment_terms: event.target.value || null })} /></label>
      <p className="proof-wide text-xs text-muted">Enter amounts and terms from the contract. Missing billing data blocks invoice drafting.</p>
      <div className="proof-actions proof-wide"><Button type="submit" variant="primary" loading={action.busy}>Create milestone</Button><Button type="button" onClick={() => setShowForm(false)}>Cancel</Button></div>
    </fieldset></form>}
    <ActionFeedback action={action} />
    {!data.milestones.length && <EmptyState>No milestones yet. Create a milestone and assign its requirements in SRS & requirements.</EmptyState>}
    <div className="mt-5 space-y-5">{data.milestones.map((milestone) => <MilestoneCard key={milestone.id} milestone={milestone} data={data} refresh={refresh} />)}</div>
  </ProofSection>;
}
