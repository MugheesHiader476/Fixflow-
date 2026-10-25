"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeading } from "@/components/layout/page-heading";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { listKnowledgeSources } from "@/lib/api";
import { cancelProofJob, projectWorkspace } from "@/lib/proofos";
import type { ProjectWorkspace } from "@/lib/proofos-contracts";
import { useResource } from "@/lib/use-resource";
import { EvidenceExplorer } from "./evidence";
import { Milestones, VerificationDetails } from "./milestones";
import { Packages } from "./packages";
import { Requirements } from "./requirements";
import { ActionFeedback, EmptyState, ErrorState, formatDate, ProofSection, StatusBadge, useProofAction } from "./shared";

function useWorkspace(projectId: string) {
  const [data, setData] = useState<ProjectWorkspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const failures = useRef(0);
  const [paused, setPaused] = useState(false);
  const reload = useCallback(() => { failures.current = 0; setPaused(false); setError(null); setVersion((current) => current + 1); }, []);
  useEffect(() => {
    const controller = new AbortController();
    void projectWorkspace(projectId, controller.signal).then((value) => { if (!controller.signal.aborted) { setData(value); setError(null); } })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not load this project."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, version]);
  const active = data?.jobs.some((job) => ["queued", "running"].includes(job.status)) ?? false;
  useEffect(() => {
    if (!active || paused) return;
    const controller = new AbortController();
    let polling = false;
    const timer = setInterval(async () => {
      if (polling || failures.current >= 3) return;
      polling = true;
      try {
        const current = await projectWorkspace(projectId, controller.signal);
        if (!controller.signal.aborted) { setData(current); failures.current = 0; setError(null); }
      } catch (failure) {
        if (!controller.signal.aborted && ++failures.current >= 3) { setPaused(true); setError(failure instanceof Error ? failure.message : "Project status refresh stopped. Use Refresh project to retry."); }
      } finally { polling = false; }
    }, 2000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [active, paused, projectId, version]);
  return { data, loading, error, reload, paused };
}

function Overview({ data }: { data: ProjectWorkspace }) {
  const totals = [
    { label: "Milestones", count: data.milestones.length },
    { label: "Technical checks passed", count: data.milestones.filter((item) => item.latest_verification?.result.technical_status === "PASSED").length },
    { label: "Blocked or failed", count: data.milestones.filter((item) => item.latest_verification && [item.latest_verification.result.technical_status, item.latest_verification.result.delivery_status].some((status) => ["BLOCKED", "FAILED"].includes(status))).length },
    { label: "Pending approvals", count: data.approvals.filter((item) => item.status === "pending").length },
    { label: "Invoice ready", count: data.milestones.filter((item) => item.readiness.eligible).length },
  ];
  return <>
    <div className="proof-stats">{totals.map((item) => <div key={item.label}><strong>{item.count}</strong><span>{item.label}</span></div>)}</div>
    <ProofSection title="Project scope"><dl className="proof-facts"><div><dt>Customer</dt><dd>{data.project.customer_name}</dd></div><div><dt>Repository</dt><dd>{data.project.repository_name ?? "Not associated"}</dd></div><div><dt>Branch</dt><dd>{data.project.branch}</dd></div><div><dt>Customer acceptance authority</dt><dd>{data.project.customer_approver_id ?? "Not configured; customer approval unavailable"}</dd></div></dl><p className="proof-description">{data.project.description || "No project description supplied."}</p><p className="proof-description">Project access is restricted to its owner. Customer reviewers receive only their addressed package requests. Additional project membership roles are unavailable.</p></ProofSection>
    <ProofSection title="Next steps"><ol className="proof-steps"><li>Upload and parse a contract or SRS in <Link href="/sources" className="text-accent underline">Sources</Link>.</li><li>Extract requirements, review their exact wording and approve a contract revision.</li><li>Create milestones, assign approved requirements and freeze their scope.</li><li>Collect native GitHub and authorized delivery evidence, then run verification.</li><li>Review the acceptance package internally and request customer acceptance.</li><li>Prepare an invoice draft only when the recorded prerequisites pass.</li></ol></ProofSection>
    <ProofSection title="Recent verification activity">{!data.verifications.length && <EmptyState>No verification executions recorded.</EmptyState>}{data.verifications.slice(0, 5).map((verification) => <details className="proof-requirement" key={verification.id}><summary><span>{data.milestones.find((item) => item.id === verification.milestone_id)?.code ?? verification.milestone_id}</span><StatusBadge status={verification.result.technical_status} /><span className="text-xs text-muted">{formatDate(verification.created_at)}</span></summary><div className="proof-requirement-body"><VerificationDetails verification={verification} /></div></details>)}</ProofSection>
  </>;
}

function Activity({ data, refresh }: { data: ProjectWorkspace; refresh: () => void }) {
  const action = useProofAction(refresh);
  return <>
    <ProofSection title="Durable processing" description="Jobs are persisted and resumed by the worker. Queued work is not a completed operation.">
      {!data.jobs.length && <EmptyState>No jobs submitted.</EmptyState>}
      {data.jobs.map((job) => <div key={job.id} className="proof-list-row"><div><h3>{job.kind.replaceAll("_", " ")}</h3><p className="break-all text-xs text-muted">{job.id} · {formatDate(job.created_at)}</p>{job.error && <p className="mt-2 break-words text-sm text-danger">{job.error}</p>}</div><StatusBadge status={job.status} />{["queued", "running"].includes(job.status) && <Button size="sm" disabled={action.busy} onClick={() => void action.perform(() => cancelProofJob(job.id), "Cancellation recorded.")}>Cancel job</Button>}</div>)}<ActionFeedback action={action} />
    </ProofSection>
    <ProofSection title="Audit history" description="Owner actions and acceptance decisions retain their recorded identities and timestamps.">
      {!data.audit.length && <EmptyState>No audit events recorded.</EmptyState>}{data.audit.map((event, index) => <div className="proof-list-row" key={typeof event.id === "string" ? event.id : index}><div><h3>{typeof event.event === "string" ? event.event.replaceAll("_", " ") : "Recorded event"}</h3><p className="break-all text-xs text-muted">Actor {typeof event.actor_id === "string" ? event.actor_id : "system"} · {typeof event.created_at === "string" ? formatDate(event.created_at) : "Unknown date"}</p><pre className="mt-2 whitespace-pre-wrap break-words text-xs text-muted">{JSON.stringify(event.details, null, 2)}</pre></div></div>)}
    </ProofSection>
  </>;
}

const tabs = [{ id: "overview", label: "Overview" }, { id: "requirements", label: "SRS & requirements" }, { id: "milestones", label: "Milestones" }, { id: "evidence", label: "Evidence" }, { id: "packages", label: "Packages & approvals" }, { id: "activity", label: "Activity" }];

export function ProjectWorkspaceView({ projectId }: { projectId: string }) {
  const resource = useWorkspace(projectId);
  const sources = useResource(listKnowledgeSources);
  const [tab, setTab] = useState("overview");
  const data = resource.data;
  const accessible = sources.data?.filter((source) => source.status === "parsed" && source.access_available !== false && source.is_active !== false) ?? [];
  return <AppShell sessionTitle={data?.project.name ?? "Project workspace"}><div className="ff-page proof-workspace">
    <Link href="/projects" className="text-xs text-muted underline">← All projects</Link>
    <PageHeading eyebrow="REQUIREMENTS · EVIDENCE · ACCEPTANCE" title={data?.project.name ?? "Project workspace"} action={<Button onClick={() => { resource.reload(); sources.reload(); }}>Refresh project</Button>}>{data ? `${data.project.customer_name} · Contractual scope and verification history` : "Load the authorized project and its persisted activity."}</PageHeading>
    {resource.loading && <p role="status" className="my-6 text-sm text-muted">Loading project workspace…</p>}
    {resource.error && <ErrorState error={resource.error} retry={resource.reload} />}
    {resource.paused && <p role="status" className="my-3 text-sm text-warning">Automatic refresh paused after three failures. Refresh project to resume.</p>}
    {data && <>{data.coverage && <p className="proof-description">{data.coverage}. Counts reflect the records shown.</p>}<div className="proof-tabs"><Tabs items={tabs} value={tab} onChange={setTab} /></div><div role="tabpanel" aria-label={tabs.find((item) => item.id === tab)?.label}>
      {tab === "overview" && <Overview data={data} />}
      {tab === "requirements" && <Requirements data={data} sources={accessible} refresh={resource.reload} />}
      {tab === "milestones" && <Milestones data={data} refresh={resource.reload} />}
      {tab === "evidence" && <EvidenceExplorer data={data} sources={accessible} refresh={resource.reload} />}
      {tab === "packages" && <Packages data={data} refresh={resource.reload} />}
      {tab === "activity" && <Activity data={data} refresh={resource.reload} />}
      {sources.error && ["requirements", "evidence"].includes(tab) && <ErrorState error={sources.error} retry={sources.reload} />}
    </div></>}
  </div></AppShell>;
}
