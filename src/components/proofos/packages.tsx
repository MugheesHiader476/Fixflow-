"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { packageDetail, packageDownload, requestApproval, reviewPackage } from "@/lib/proofos";
import type { AcceptancePackage, JsonObject, JsonValue, PackageDetail, ProjectWorkspace } from "@/lib/proofos-contracts";
import { ActionFeedback, EmptyState, formatDate, inputClass, ProofSection, StatusBadge, useProofAction } from "./shared";

function DataValue({ value }: { value: JsonValue }) {
  if (value === null) return <span className="text-muted">Not supplied</span>;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  if (Array.isArray(value)) return value.length ? <ol className="proof-preview-list">{value.map((item, index) => <li key={index}><DataValue value={item} /></li>)}</ol> : <span className="text-muted">None recorded</span>;
  if (typeof value === "object") return <StructuredPreview value={value} />;
  return <span className="whitespace-pre-wrap break-words">{String(value)}</span>;
}
export function StructuredPreview({ value }: { value: JsonObject }) {
  return <dl className="proof-preview">{Object.entries(value).map(([name, item]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd><DataValue value={item} /></dd></div>)}</dl>;
}

function PackageCard({ value, data, refresh }: { value: AcceptancePackage; data: ProjectWorkspace; refresh: () => void }) {
  const [preview, setPreview] = useState<PackageDetail | null>(null);
  const [audience, setAudience] = useState<"customer" | "internal">("customer");
  const [reason, setReason] = useState("");
  const action = useProofAction(refresh);
  const milestone = data.milestones.find((item) => item.id === value.milestone_id);
  const verification = data.verifications.find((item) => item.id === value.verification_id);
  const stale = milestone?.latest_verification && milestone.latest_verification.id !== value.verification_id;
  return <article className="proof-package"><div className="proof-list-row"><div><p className="font-mono text-xs text-muted">{milestone?.code ?? value.milestone_id}</p><h3>Acceptance package · version {value.version}</h3><p className="text-xs text-muted">Generated {formatDate(value.created_at)}</p></div><div className="proof-actions"><span className="text-xs">Internal</span><StatusBadge status={value.internal_review} /><span className="text-xs">Customer</span><StatusBadge status={value.customer_status} /></div></div>
    {stale && <p className="my-3 text-sm text-warning">A newer verification exists. This package preserves the earlier execution and must not be treated as current.</p>}
    <p className="proof-description">Technical: {verification?.result.technical_status ?? "Unavailable"} · Delivery: {verification?.result.delivery_status ?? "Unavailable"}. Generating a package does not establish customer acceptance.</p>
    <div className="proof-actions my-4"><Button size="sm" disabled={action.busy} onClick={() => void action.perform(async () => { setPreview(await packageDetail(value.id)); }, "Package preview loaded.")}>Preview package</Button>
      {(["customer", "internal"] as const).map((variant) => <span className="proof-actions" key={variant}><a className="proof-download" href={packageDownload(value.id, variant, "pdf")} download>{variant === "customer" ? "Customer" : "Internal"} PDF</a><a className="proof-download" href={packageDownload(value.id, variant, "json")} download>{variant === "customer" ? "Customer" : "Internal"} JSON</a></span>)}
    </div>
    {preview && <div className="proof-package-preview"><div className="proof-actions"><h4>ProofOS · {data.project.name}</h4><Button size="sm" variant="ghost" onClick={() => setPreview(null)}>Close preview</Button></div><label className="mt-3 block text-sm">Package audience<select className={inputClass} value={audience} onChange={(event) => setAudience(event.target.value === "internal" ? "internal" : "customer")}><option value="customer">Customer-safe variant</option><option value="internal">Internal variant</option></select></label><StructuredPreview value={preview[audience]} /></div>}
    <form className="proof-form mt-5" onSubmit={(event) => {
      event.preventDefault(); const submitter = (event.nativeEvent as SubmitEvent).submitter;
      const decision = submitter instanceof HTMLButtonElement && submitter.value === "rejected" ? "rejected" : "approved";
      void action.perform(() => reviewPackage(value.id, value.internal_hash, decision, reason), `Internal review ${decision} for package version ${value.version}.`);
    }}><fieldset disabled={action.busy || value.internal_review !== "pending"}>
      <label>Internal review reason<textarea required minLength={1} maxLength={2000} rows={2} className={inputClass} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <p className="proof-description break-all">Review binds the exact internal package SHA-256: {value.internal_hash}</p>
      <div className="proof-actions"><Button type="submit" value="approved" disabled={!reason.trim()}>Approve internal review</Button><Button type="submit" value="rejected" variant="danger" disabled={!reason.trim()}>Reject internal review</Button></div>
    </fieldset></form>
    <div className="mt-5"><Button disabled={action.busy || value.internal_review !== "approved" || Boolean(stale) || !data.project.customer_approver_id} onClick={() => void action.perform(() => requestApproval(value.id), "Customer approval request recorded for the configured authenticated approver. No external notification was sent.")}>Request customer approval</Button><p className="proof-description">The configured customer account reviews its bound package in Approval requests. Requests expire after 72 hours.</p></div>
    <ActionFeedback action={action} />
  </article>;
}

export function Packages({ data, refresh }: { data: ProjectWorkspace; refresh: () => void }) {
  return <>
    <ProofSection title="Acceptance packages" description="Versioned internal and customer-safe packages preserve verification history and separate review decisions.">
      {!data.packages.length && <EmptyState>No packages yet. Run milestone verification, then prepare an acceptance package from Milestones.</EmptyState>}
      <div className="space-y-5">{data.packages.map((value) => <PackageCard key={value.id} value={value} data={data} refresh={refresh} />)}</div>
    </ProofSection>
    <ProofSection title="Controlled delivery" description="Downloads stay private and reauthorize access. Customer review requires the configured authenticated account."><p className="text-sm text-muted">Slack notification delivery and Drive package uploads are unavailable. Use the private PDF and JSON downloads above. Invoice sending and payment processing are unavailable.</p></ProofSection>
    <ProofSection title="Customer approval history">{!data.approvals.length && <EmptyState>No customer approval requests recorded.</EmptyState>}{data.approvals.map((approval) => <div className="proof-list-row" key={approval.id}><div><h3>{approval.title ?? "Customer approval request"}</h3><p className="break-all text-xs text-muted">Package {approval.package_id} · {approval.package_hash}</p><p className="text-xs text-muted">Expires {formatDate(approval.expires_at)}{approval.decided_at ? ` · Decided ${formatDate(approval.decided_at)}` : ""}</p></div><StatusBadge status={approval.status} /></div>)}</ProofSection>
  </>;
}
