"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeading } from "@/components/layout/page-heading";
import { Button } from "@/components/ui/button";
import { approvalDetail, approvalDownload, decideApproval, listApprovals } from "@/lib/proofos";
import type { ApprovalDetail, ApprovalRequest } from "@/lib/proofos-contracts";
import { useResource } from "@/lib/use-resource";
import { StructuredPreview } from "./packages";
import { ActionFeedback, EmptyState, ErrorState, formatDate, inputClass, ProofSection, StatusBadge, useProofAction } from "./shared";

function ApprovalCard({ approval, refresh }: { approval: ApprovalRequest; refresh: () => void }) {
  const [detail, setDetail] = useState<ApprovalDetail | null>(null);
  const [reason, setReason] = useState("");
  const action = useProofAction(refresh);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 30000); return () => clearInterval(timer); }, []);
  const expired = new Date(approval.expires_at).getTime() <= now;
  return <article className="proof-package"><div className="proof-list-row"><div><h3>{approval.title ?? "Acceptance package review"}</h3><p className="text-sm text-muted">{approval.customer_name ?? "Configured customer"} · Expires {formatDate(approval.expires_at)}</p></div><StatusBadge status={approval.status} /></div>
    <p className="proof-description break-all">Exact customer package SHA-256: {approval.package_hash}</p>
    <Button size="sm" disabled={action.busy} onClick={() => void action.perform(async () => { setDetail(await approvalDetail(approval.id)); }, "Customer-safe package loaded for review.")}>Review customer package</Button>
    {detail && <div className="proof-package-preview mt-4"><h4>ProofOS · Customer acceptance review</h4><div className="proof-actions my-3"><a className="proof-download" href={approvalDownload(approval.id, "pdf")} download>Download customer PDF</a><a className="proof-download" href={approvalDownload(approval.id, "json")} download>Download customer JSON</a></div><StructuredPreview value={detail.package} /></div>}
    {expired && approval.status === "pending" && <p className="my-3 text-sm text-warning">This approval request has expired. Ask the project owner for a new request.</p>}
    {approval.decided_at && <p className="proof-description">Decision recorded {formatDate(approval.decided_at)}.</p>}
    <form className="proof-form mt-4" onSubmit={(event) => {
      event.preventDefault(); const submitter = (event.nativeEvent as SubmitEvent).submitter;
      const value = submitter instanceof HTMLButtonElement ? submitter.value : "";
      if (value !== "accepted" && value !== "rejected" && value !== "rework_requested") return;
      void action.perform(() => decideApproval(approval.id, approval.package_hash, value, reason), `Your ${value.replaceAll("_", " ")} decision was recorded against this exact package.`);
    }}><fieldset disabled={action.busy || expired || approval.status !== "pending" || !detail}>
      <label>Decision reason<textarea required minLength={1} maxLength={2000} rows={3} className={inputClass} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <p className="proof-description">Your authenticated account is the configured acceptance authority. This decision applies only to the reviewed package version.</p>
      <div className="proof-actions"><Button type="submit" value="accepted" variant="primary" disabled={!reason.trim()}>Accept package</Button><Button type="submit" value="rejected" variant="danger" disabled={!reason.trim()}>Reject package</Button><Button type="submit" value="rework_requested" disabled={!reason.trim()}>Request rework</Button></div>
    </fieldset></form><ActionFeedback action={action} />
  </article>;
}

export function Approvals() {
  const resource = useResource(listApprovals);
  return <AppShell><div className="ff-page"><PageHeading eyebrow="AUTHENTICATED CUSTOMER REVIEW" title="Approval requests" action={<Button onClick={resource.reload}>Refresh approvals</Button>}>Inspect the customer-safe package before recording acceptance, rejection or a rework request.</PageHeading>
    {resource.loading && <p role="status" className="my-6 text-sm text-muted">Loading your approval requests…</p>}
    {resource.error && <ErrorState error={resource.error} retry={resource.reload} />}
    {resource.data && <ProofSection title="Requests addressed to your account" description="Only the configured acceptance authority can decide these requests.">{!resource.data.length && <EmptyState>No approval requests are addressed to your authenticated account.</EmptyState>}<div className="space-y-5">{resource.data.map((approval) => <ApprovalCard key={approval.id} approval={approval} refresh={resource.reload} />)}</div></ProofSection>}
  </div></AppShell>;
}
