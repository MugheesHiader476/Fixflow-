"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { addMilestoneEvidence, collectEvidence } from "@/lib/proofos";
import type { ProjectWorkspace } from "@/lib/proofos-contracts";
import type { KnowledgeSource } from "@/lib/types";
import { safeExternalUrl } from "@/lib/utils";
import { ActionFeedback, EmptyState, formatDate, inputClass, ProofSection, StatusBadge, useProofAction } from "./shared";

export function EvidenceExplorer({ data, sources, refresh }: { data: ProjectWorkspace; sources: KnowledgeSource[]; refresh: () => void }) {
  const [milestoneId, setMilestoneId] = useState(data.milestones[0]?.id ?? "");
  const [runId, setRunId] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [artifacts, setArtifacts] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [kind, setKind] = useState<"document" | "discussion">("document");
  const [requirementIds, setRequirementIds] = useState<string[]>([]);
  const [customerSafe, setCustomerSafe] = useState(false);
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("");
  const [requirementFilter, setRequirementFilter] = useState("");
  const action = useProofAction(refresh);
  function toggleRequirement(id: string, checked: boolean) {
    setRequirementIds((current) => checked ? [...current, id] : current.filter((value) => value !== id));
  }
  const requirements = data.requirements.filter((item) => item.milestone_id === milestoneId);
  const filteredRequirement = data.requirements.find((item) => item.id === requirementFilter);
  const records = data.evidence.filter((item) => (!filter || [item.fact.provider, item.fact.kind, item.fact.title].join(" ").toLowerCase().includes(filter.toLowerCase()))
    && (!status || item.fact.status === status) && (!filteredRequirement || item.fact.requirement_ids.includes(filteredRequirement.id) || filteredRequirement.acceptance_criteria.some((criterion) => item.fact.requirement_ids.includes(criterion.id))));
  return <>
    <ProofSection title="Collect milestone evidence" description="Native GitHub collection checks the selected repository and exact run. Parsed documents and discussions remain separately identified.">
      <label className="block max-w-xl">Milestone<select className={inputClass} value={milestoneId} onChange={(event) => { setMilestoneId(event.target.value); setRequirementIds([]); }}><option value="">Choose a milestone</option>{data.milestones.map((milestone) => <option key={milestone.id} value={milestone.id}>{milestone.code} · {milestone.title}</option>)}</select></label>
      <div className="proof-project-grid mt-5">
        <form className="proof-form" onSubmit={(event) => { event.preventDefault(); void action.perform(() => collectEvidence(milestoneId, { run_id: Number(runId), workflow_id: Number(workflowId), artifact_names: artifacts.split("\n").map((name) => name.trim()).filter(Boolean) }), "GitHub evidence collection queued. Provider results will be recorded by the worker."); }}>
          <h3>GitHub Actions evidence</h3><p className="proof-description">{data.project.repository_name ? `${data.project.repository_name} · ${data.project.branch}` : "Associate a selected repository when creating a project to enable native collection."}</p>
          <fieldset disabled={action.busy || !data.project.repository_id} className="space-y-3">
            <label className="block">Workflow ID<input required min={1} type="number" step={1} className={inputClass} value={workflowId} onChange={(event) => setWorkflowId(event.target.value)} /></label>
            <label className="block">Actions run ID<input required min={1} type="number" step={1} className={inputClass} value={runId} onChange={(event) => setRunId(event.target.value)} /></label>
            <label className="block">Test artifact names (one per line, up to five)<textarea rows={3} maxLength={2000} className={inputClass} value={artifacts} placeholder="The exact permitted JUnit artifact names" onChange={(event) => setArtifacts(event.target.value)} /></label>
            <Button type="submit" disabled={!milestoneId || !workflowId || !runId} loading={action.busy}>Collect native GitHub evidence</Button>
          </fieldset>
        </form>
        <form className="proof-form" onSubmit={(event) => { event.preventDefault(); void action.perform(() => addMilestoneEvidence(milestoneId, { source_id: sourceId, requirement_ids: requirementIds, kind, customer_safe: customerSafe }), "Source evidence collection queued."); }}>
          <h3>Delivery documents & discussions</h3><fieldset disabled={action.busy} className="space-y-3">
            <label className="block">Evidence source<select required className={inputClass} value={sourceId} onChange={(event) => setSourceId(event.target.value)}><option value="">Choose an authorized parsed source</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
            <label className="block">Evidence kind<select className={inputClass} value={kind} onChange={(event) => setKind(event.target.value === "discussion" ? "discussion" : "document")}><option value="document">Delivery document</option><option value="discussion">Discussion context</option></select></label>
            <fieldset className="proof-policy"><legend>Linked requirements</legend>{requirements.map((requirement) => <label key={requirement.id} className="proof-checkbox"><input type="checkbox" checked={requirementIds.includes(requirement.id)} onChange={(event) => toggleRequirement(requirement.id, event.target.checked)} />{requirement.stable_id} · {requirement.original_text.slice(0, 90)}</label>)}{!requirements.length && <p className="text-xs text-muted">Assign requirements to this milestone first.</p>}</fieldset>
            <label className="proof-checkbox"><input type="checkbox" checked={customerSafe} onChange={(event) => setCustomerSafe(event.target.checked)} />Include this evidence in the customer-safe package</label>
            <Button type="submit" disabled={!milestoneId || !sourceId || !requirementIds.length} loading={action.busy}>Add source evidence</Button>
          </fieldset>
        </form>
      </div><ActionFeedback action={action} />
    </ProofSection>
    <ProofSection title="Evidence explorer" description="Observed evidence is a snapshot. Its presence or provider status alone does not establish requirement satisfaction.">
      <div className="proof-form-grid mb-5">
        <label>Filter source or title<input type="search" className={inputClass} value={filter} onChange={(event) => setFilter(event.target.value)} /></label>
        <label>Evidence status<select className={inputClass} value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All evidence statuses</option>{["passed", "failed", "pending", "unknown", "skipped"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Requirement<select className={inputClass} value={requirementFilter} onChange={(event) => setRequirementFilter(event.target.value)}><option value="">All requirements</option>{data.requirements.map((item) => <option key={item.id} value={item.id}>{item.stable_id}</option>)}</select></label>
      </div>
      {!records.length && <EmptyState>{data.evidence.length ? "No evidence matches these filters." : "No collected evidence yet. Queue a native run or add an authorized delivery source."}</EmptyState>}
      {records.map((item) => {
        const fact = item.fact;
        const url = fact.url ? safeExternalUrl(fact.url) : null;
        return <details key={item.id} id={`evidence-${item.id}`} className="proof-requirement"><summary><span>{fact.title}</span><span className="text-xs text-muted">{fact.provider} · {fact.kind}</span>{item.current === false && <StatusBadge status="HISTORICAL" />}<StatusBadge status={fact.status} /></summary><div className="proof-requirement-body">
          <p className="whitespace-pre-wrap break-words text-sm">{fact.details}</p>
          <dl className="proof-facts mt-4"><div><dt>Repository / branch</dt><dd>{fact.repository_id ?? "Not applicable"} / {fact.branch ?? "Not recorded"}</dd></div><div><dt>Commit SHA</dt><dd className="font-mono">{fact.sha ?? "Not recorded"}</dd></div><div><dt>Workflow / run</dt><dd>{fact.workflow_id ?? "—"} / {fact.run_id ?? "—"}</dd></div><div><dt>Test suite / cases</dt><dd>{fact.suite ?? "Not recorded"} · {fact.cases.join(", ") || "No case evidence"}</dd></div><div><dt>Observed</dt><dd>{formatDate(fact.observed_at)}</dd></div><div><dt>Provider event</dt><dd>{fact.event_at ? formatDate(fact.event_at) : "Not recorded"}</dd></div><div><dt>Native verification / complete</dt><dd>{fact.verified_native ? "Native identity verified" : "No native verification"} / {fact.complete ? "Complete observation" : "Incomplete observation"}</dd></div><div><dt>Customer visibility</dt><dd>{fact.customer_safe ? "Customer-safe" : "Internal only"}</dd></div><div><dt>Source hash</dt><dd className="font-mono">{fact.source_hash ?? "Not recorded"}</dd></div><div><dt>Linked requirement / criterion IDs</dt><dd>{fact.requirement_ids.join(", ") || "No explicit links"}</dd></div></dl>
          {url && <a className="mt-4 inline-block text-sm text-accent underline" href={url} target="_blank" rel="noopener noreferrer">Open original resource ↗</a>}
        </div></details>;
      })}
    </ProofSection>
  </>;
}
