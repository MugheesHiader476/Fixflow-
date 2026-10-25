"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { approveContract, assignRequirement, extractContract, reviewRequirement } from "@/lib/proofos";
import type { ContractVersion, Criterion, EngineeringLink, ProjectWorkspace, Requirement } from "@/lib/proofos-contracts";
import type { KnowledgeSource } from "@/lib/types";
import { safeExternalUrl } from "@/lib/utils";
import { ActionFeedback, EmptyState, inputClass, ProofSection, StatusBadge, useProofAction } from "./shared";

function nullable(value: string): string | null { return value.trim() || null; }
function numberOrNull(value: string): number | null { return value ? Number(value) : null; }

function TraceabilityRow({ requirement, criterion, data }: { requirement: Requirement; criterion: Criterion; data: ProjectWorkspace }) {
  const milestone = data.milestones.find((item) => item.id === requirement.milestone_id);
  const check = milestone?.latest_verification?.result.checks.find((item) => item.criterion_id === criterion.id);
  const evidence = data.evidence.filter((item) => item.milestone_id === milestone?.id && (check?.evidence_ids.includes(item.fact.id) || item.fact.requirement_ids.includes(criterion.id) || item.fact.requirement_ids.includes(requirement.id)));
  return <tr><td><span className="font-mono">{requirement.stable_id}</span><p className="mt-2 text-xs text-muted">{requirement.state.replaceAll("_", " ")}</p></td><td>{milestone?.code ?? "Unassigned"}<p className="mt-2 text-xs text-muted">{milestone?.expected_sha?.slice(0, 12) ?? "No commit bound"}</p></td><td>{criterion.description}<p className="mt-2 text-xs text-muted">{criterion.kind}{criterion.suite ? ` · ${criterion.suite}` : ""}{criterion.environment ? ` · ${criterion.environment}` : ""}</p></td><td>{requirement.engineering_links.length ? requirement.engineering_links.map((link) => {
    const url = link.url ? safeExternalUrl(link.url) : null;
    return <p key={`${link.kind}-${link.external_id}`} className="mb-2">{url ? <a href={url} className="text-accent underline" target="_blank" rel="noopener noreferrer">{link.kind.replaceAll("_", " ")} {link.external_id}</a> : `${link.kind.replaceAll("_", " ")} ${link.external_id}`}</p>;
  }) : <span className="text-muted">No mapping</span>}</td><td>{evidence.length ? evidence.map((item) => <p key={item.id} className="mb-2">{item.fact.title}<span className="block text-xs text-muted">{item.fact.provider} · {item.fact.suite ?? item.fact.kind}</span></p>) : <span className="text-muted">No linked evidence</span>}</td><td><StatusBadge status={check?.status ?? "UNVERIFIED"} /><p className="mt-2 text-xs text-muted">{check?.explanation ?? "No verification decision recorded for this condition."}</p></td></tr>;
}

function TraceabilityMatrix({ data }: { data: ProjectWorkspace }) {
  return <div className="proof-table-wrap"><table className="proof-table"><caption className="sr-only">Requirement to milestone, engineering reference, evidence and verification traceability</caption><thead><tr><th>Requirement</th><th>Milestone / commit</th><th>Acceptance condition</th><th>Engineering references</th><th>Evidence</th><th>Verification decision</th></tr></thead><tbody>{data.requirements.flatMap((requirement) => requirement.acceptance_criteria.map((criterion) => <TraceabilityRow key={`${requirement.id}-${criterion.id}`} requirement={requirement} criterion={criterion} data={data} />))}</tbody></table></div>;
}

function CriterionEditor({ value, update, remove }: { value: Criterion; update: (criterion: Criterion) => void; remove: () => void }) {
  return <fieldset className="proof-policy"><legend>{value.id}</legend>
    <div className="proof-form-grid">
      <label className="proof-wide">Acceptance condition<textarea required rows={2} maxLength={4000} className={inputClass} value={value.description} onChange={(event) => update({ ...value, description: event.target.value })} /></label>
      <label>Category<select className={inputClass} value={value.category} onChange={(event) => { const category = event.target.value; if (category === "technical" || category === "delivery" || category === "customer") update({ ...value, category }); }}><option value="technical">Technical verification</option><option value="delivery">Delivery verification</option><option value="customer">Customer acceptance</option></select></label>
      <label>Evidence policy<select className={inputClass} value={value.kind} onChange={(event) => {
        const kind = event.target.value;
        if (kind === "ci" || kind === "deployment" || kind === "document" || kind === "review" || kind === "discussion" || kind === "manual") update({ ...value, kind });
      }}><option value="ci">CI and automated tests</option><option value="deployment">Deployment</option><option value="document">Delivery document</option><option value="review">Repository review</option><option value="discussion">Discussion evidence</option><option value="manual">Manual review required</option></select></label>
      <label>Repository ID<input maxLength={300} className={inputClass} value={value.repository_id ?? ""} onChange={(event) => update({ ...value, repository_id: nullable(event.target.value) })} /></label>
      <label>Required branch<input maxLength={300} className={inputClass} value={value.branch ?? ""} onChange={(event) => update({ ...value, branch: nullable(event.target.value) })} /></label>
      <label className="proof-wide">Required commit SHA<input maxLength={100} className={`${inputClass} font-mono`} value={value.sha ?? ""} onChange={(event) => update({ ...value, sha: nullable(event.target.value) })} /></label>
      <label>Workflow ID<input type="number" min={1} step={1} className={inputClass} value={value.workflow_id ?? ""} onChange={(event) => update({ ...value, workflow_id: numberOrNull(event.target.value) })} /></label>
      <label>Required run ID<input type="number" min={1} step={1} className={inputClass} value={value.run_id ?? ""} onChange={(event) => update({ ...value, run_id: numberOrNull(event.target.value) })} /></label>
      <label>Test suite<input maxLength={300} className={inputClass} value={value.suite ?? ""} onChange={(event) => update({ ...value, suite: nullable(event.target.value) })} /></label>
      <label>Deployment environment<input maxLength={300} className={inputClass} placeholder="staging" value={value.environment ?? ""} onChange={(event) => update({ ...value, environment: nullable(event.target.value) })} /></label>
      <label>Evidence freshness (hours)<input required type="number" min={1} max={8760} step={1} className={inputClass} value={value.max_age_hours} onChange={(event) => update({ ...value, max_age_hours: Number(event.target.value) })} /></label>
      <label className="proof-wide">Required test case names (one per line)<textarea rows={2} className={inputClass} value={value.cases.join("\n")} onChange={(event) => update({ ...value, cases: event.target.value.split("\n").filter((item) => item.trim()) })} /></label>
      <label className="proof-checkbox"><input type="checkbox" checked={value.mandatory} onChange={(event) => update({ ...value, mandatory: event.target.checked })} />Mandatory condition</label>
      <label className="proof-checkbox"><input type="checkbox" checked={value.allow_skipped} onChange={(event) => update({ ...value, allow_skipped: event.target.checked })} />Allow skipped tests</label>
    </div>
    <Button type="button" size="sm" variant="ghost" className="mt-3" onClick={remove}>Remove condition</Button>
  </fieldset>;
}

function LinkEditor({ value, update, remove }: { value: EngineeringLink; update: (link: EngineeringLink) => void; remove: () => void }) {
  return <fieldset className="proof-policy"><legend>Engineering reference</legend><div className="proof-form-grid">
    <label>Reference type<select className={inputClass} value={value.kind} onChange={(event) => {
      const kind = event.target.value;
      if (kind === "issue" || kind === "pull_request" || kind === "commit" || kind === "release" || kind === "task") update({ ...value, kind });
    }}><option value="task">Engineering task</option><option value="issue">GitHub issue</option><option value="pull_request">Pull request</option><option value="commit">Commit</option><option value="release">Release</option></select></label>
    <label>External ID<input required maxLength={200} className={inputClass} value={value.external_id} onChange={(event) => update({ ...value, external_id: event.target.value })} /></label>
    <label>Title<input required maxLength={300} className={inputClass} value={value.title} onChange={(event) => update({ ...value, title: event.target.value })} /></label>
    <label>HTTPS source URL (optional)<input type="url" pattern="https://.*" maxLength={2000} className={inputClass} value={value.url ?? ""} onChange={(event) => update({ ...value, url: nullable(event.target.value) })} /></label>
  </div><Button type="button" size="sm" variant="ghost" className="mt-3" onClick={remove}>Remove reference</Button></fieldset>;
}

function RequirementReview({ requirement, contract, data, refresh }: { requirement: Requirement; contract: ContractVersion | undefined; data: ProjectWorkspace; refresh: () => void }) {
  const [interpretation, setInterpretation] = useState(requirement.interpretation);
  const [ambiguities, setAmbiguities] = useState(requirement.ambiguities.join("\n"));
  const [criteria, setCriteria] = useState(requirement.acceptance_criteria);
  const [links, setLinks] = useState(requirement.engineering_links);
  const [milestoneId, setMilestoneId] = useState(requirement.milestone_id ?? "");
  const action = useProofAction(refresh);
  const editable = contract?.state === "DRAFT";
  function updateCriterion(index: number, next: Criterion) { setCriteria((current) => current.map((item, position) => position === index ? next : item)); }
  function removeCriterion(index: number) { setCriteria((current) => current.filter((_, position) => position !== index)); }
  function updateLink(index: number, next: EngineeringLink) { setLinks((current) => current.map((item, position) => position === index ? next : item)); }
  function removeLink(index: number) { setLinks((current) => current.filter((_, position) => position !== index)); }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const approve = submitter instanceof HTMLButtonElement && submitter.value === "approve";
    void action.perform(() => reviewRequirement(requirement.id, { interpretation, ambiguities: ambiguities.split("\n").filter((item) => item.trim()), acceptance_criteria: criteria, engineering_links: links, approve }), approve ? "Requirement approved by you." : "Requirement review saved.");
  }
  return <details className="proof-requirement"><summary><span className="font-mono text-xs">{requirement.stable_id}</span><span>{requirement.original_text.slice(0, 140)}</span><StatusBadge status={requirement.state} /></summary>
    <div className="proof-requirement-body">
      <h3>Original contractual wording</h3><blockquote className="proof-quote">{requirement.original_text}</blockquote>
      <p className="proof-description break-all">Source {contract?.title ?? requirement.contract_id} · revision {contract?.revision ?? "unknown"} · SHA-256 {contract?.source_hash ?? "unavailable"}</p>
      <p className="proof-description break-words">Source location: {Object.entries(requirement.source_location).map(([name, value]) => `${name}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`).join(" · ") || "Not available"}</p>
      <form onSubmit={submit} className="proof-form"><fieldset disabled={action.busy || !editable}>
        <label>Proposed interpretation<textarea required rows={3} maxLength={8000} className={inputClass} value={interpretation} onChange={(event) => setInterpretation(event.target.value)} /></label>
        <label className="mt-4 block">Unresolved ambiguities (one per line)<textarea rows={2} className={inputClass} value={ambiguities} onChange={(event) => setAmbiguities(event.target.value)} /></label>
        <p className="proof-description">Extraction proposes scope. Resolve ambiguities and review every acceptance policy before approving. A discussion or manual claim does not establish technical completion.</p>
        {criteria.map((criterion, index) => <CriterionEditor key={criterion.id} value={criterion} update={(next) => updateCriterion(index, next)} remove={() => removeCriterion(index)} />)}
        <Button type="button" size="sm" onClick={() => setCriteria((current) => [...current, { id: `${requirement.stable_id}-${crypto.randomUUID().slice(0, 8)}`, description: "", category: "technical", kind: "manual", mandatory: true, repository_id: data.project.repository_id, branch: data.project.branch, sha: null, workflow_id: null, run_id: null, suite: null, environment: null, cases: [], max_age_hours: 168, allow_skipped: false }])}>Add acceptance condition</Button>
        <h3 className="mt-5">Engineering traceability</h3>
        {links.map((link, index) => <LinkEditor key={index} value={link} update={(next) => updateLink(index, next)} remove={() => removeLink(index)} />)}
        <Button type="button" size="sm" onClick={() => setLinks((current) => [...current, { kind: "task", external_id: "", title: "", url: null }])}>Add engineering reference</Button>
        <div className="proof-actions mt-5"><Button type="submit" value="save" loading={action.busy} disabled={!criteria.length}>Save review</Button><Button type="submit" value="approve" variant="primary" disabled={!criteria.length || Boolean(ambiguities.trim())}>Approve requirement</Button></div>
      </fieldset></form>
      {!editable && <p className="proof-description">This contract version is {contract?.state.toLowerCase() ?? "unavailable"}. Its requirement wording and approved policies are preserved.</p>}
      <div className="proof-form-grid mt-4"><label>Milestone assignment<select className={inputClass} value={milestoneId} onChange={(event) => setMilestoneId(event.target.value)}><option value="">Choose a milestone</option>{data.milestones.filter((milestone) => milestone.contract_id === requirement.contract_id).map((milestone) => <option key={milestone.id} value={milestone.id}>{milestone.code} · {milestone.title}{milestone.scope_approved ? " · frozen" : ""}</option>)}</select></label><div className="self-end"><Button disabled={action.busy || !milestoneId || milestoneId === requirement.milestone_id} onClick={() => void action.perform(() => assignRequirement(requirement.id, milestoneId), "Requirement assigned to milestone.")}>Assign requirement</Button></div></div>
      <ActionFeedback action={action} />
    </div>
  </details>;
}

export function Requirements({ data, sources, refresh }: { data: ProjectWorkspace; sources: KnowledgeSource[]; refresh: () => void }) {
  const [sourceId, setSourceId] = useState("");
  const [title, setTitle] = useState("");
  const action = useProofAction(refresh);
  return <>
    <ProofSection title="SRS & contract analysis" description="Each extraction creates a source-bound contract revision. The original text and proposed interpretation remain distinct.">
      <p className="proof-description"><Link href="/sources" className="text-accent underline">Upload an SRS or contract</Link>, wait for parsing, then select the authorized source below.</p>
      <form className="proof-form" onSubmit={(event) => { event.preventDefault(); void action.perform(() => extractContract(data.project.id, sourceId, title), "Requirement extraction queued. Track its persisted status in Activity."); }}><fieldset disabled={action.busy} className="proof-form-grid">
        <label>Parsed source<select required className={inputClass} value={sourceId} onChange={(event) => setSourceId(event.target.value)}><option value="">Choose an accessible parsed source</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
        <label>Contract or SRS title<input required maxLength={200} className={inputClass} value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <div className="proof-wide"><Button type="submit" variant="primary" loading={action.busy} disabled={!sourceId}>Extract requirements</Button></div>
      </fieldset></form><ActionFeedback action={action} />
    </ProofSection>
    <ProofSection title="Contract versions" description="Approve a version only after reviewing all extracted requirements. Approved revisions are immutable; amendments need a new extraction.">
      {!data.contracts.length && <EmptyState>No contract versions extracted yet.</EmptyState>}
      {data.contracts.map((contract) => <div key={contract.id} className="proof-list-row"><div><h3>{contract.title} · revision {contract.revision}</h3><p className="break-all text-xs text-muted">Source {contract.source_id} · version {contract.source_version} · {contract.source_hash}</p></div><StatusBadge status={contract.state} />{contract.state === "DRAFT" && <Button size="sm" disabled={action.busy || !data.requirements.some((item) => item.contract_id === contract.id) || data.requirements.some((item) => item.contract_id === contract.id && item.state !== "APPROVED")} onClick={() => void action.perform(() => approveContract(contract.id), "Contract version approved.")}>Approve contract version</Button>}</div>)}
    </ProofSection>
    <ProofSection title="Requirements traceability" description="Expand a requirement to inspect its exact source text, edit policies and assign its milestone.">
      {!data.requirements.length && <EmptyState>No requirements yet. Extract a parsed contract or SRS to begin.</EmptyState>}
      {!!data.requirements.length && <TraceabilityMatrix data={data} />}
      {data.requirements.map((requirement) => <RequirementReview key={`${requirement.id}-${requirement.state}-${JSON.stringify(requirement.acceptance_criteria)}`} requirement={requirement} contract={data.contracts.find((contract) => contract.id === requirement.contract_id)} data={data} refresh={refresh} />)}
    </ProofSection>
  </>;
}
