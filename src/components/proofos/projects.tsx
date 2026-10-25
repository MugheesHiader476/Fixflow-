"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { FolderKanban, Plus } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeading } from "@/components/layout/page-heading";
import { Button } from "@/components/ui/button";
import { discoverResources, listConnectors } from "@/lib/connectors";
import type { ConnectorAccount, ConnectorResource } from "@/lib/connector-contracts";
import { createProject, listProjects } from "@/lib/proofos";
import type { ProjectCreate } from "@/lib/proofos-contracts";
import { useResource } from "@/lib/use-resource";
import { ActionFeedback, EmptyState, ErrorState, formatDate, inputClass, ProofSection, useProofAction } from "./shared";

const blankProject: ProjectCreate = { name: "", customer_name: "", description: "", customer_approver_id: null, github_account_id: null, repository_id: null, repository_name: null, branch: "main" };

function RepositorySelection({ accounts, value, change }: { accounts: ConnectorAccount[]; value: ProjectCreate; change: (value: ProjectCreate) => void }) {
  const [resources, setResources] = useState<ConnectorResource[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const account = accounts.find((item) => item.id === value.github_account_id);
  useEffect(() => {
    if (!account) return;
    const request = new AbortController();
    void discoverResources(account.id, undefined, request.signal).then((page) => {
      if (!request.signal.aborted) { setResources(page.resources); setCursor(page.next_cursor); setError(null); }
    }).catch((failure: unknown) => { if (!request.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not discover repositories."); });
    return () => request.abort();
  }, [account]);
  const selected = resources.filter((item) => account?.configuration.resource_ids.includes(item.id));
  return <>
    <label>GitHub connection (optional)<select className={inputClass} value={value.github_account_id ?? ""} onChange={(event) => {
      setResources([]); setCursor(null); setError(null);
      change({ ...value, github_account_id: event.target.value || null, repository_id: null, repository_name: null });
    }}><option value="">No repository yet</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
    {account && <label>Selected repository<select required className={inputClass} value={value.repository_id ?? ""} onChange={(event) => {
      const repository = selected.find((item) => item.id === event.target.value);
      change({ ...value, repository_id: repository?.id ?? null, repository_name: repository?.name ?? null, branch: account.configuration.branches[event.target.value] || value.branch });
    }}><option value="">Choose an authorized repository</option>{selected.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
    {account && !selected.length && <p className="text-xs text-muted">Only repositories selected in Connected Apps can be associated. <Link href="/connectors" className="underline">Manage selections</Link>.</p>}
    {cursor && account && <Button type="button" size="sm" loading={loading} onClick={() => {
      setLoading(true);
      void discoverResources(account.id, cursor).then((page) => { setResources((current) => [...current, ...page.resources]); setCursor(page.next_cursor); })
        .catch((failure: unknown) => setError(failure instanceof Error ? failure.message : "Could not load more repositories.")).finally(() => setLoading(false));
    }}>Load more repositories</Button>}
    {error && <ErrorState error={error} />}
  </>;
}

export function Projects() {
  const projects = useResource(listProjects);
  const connectors = useResource(listConnectors);
  const [form, setForm] = useState<ProjectCreate>(blankProject);
  const [showForm, setShowForm] = useState(false);
  const action = useProofAction(projects.reload);
  const accounts = connectors.data?.find((item) => item.provider === "github")?.accounts.filter((item) => item.authentication_status === "valid" && ["connected", "syncing", "connected_with_warning"].includes(item.status)) ?? [];
  function submit(event: FormEvent) {
    event.preventDefault();
    void action.perform(() => createProject(form), "Project created. Add a contract or SRS to define its scope.").then((success) => { if (success) { setForm(blankProject); setShowForm(false); } });
  }
  return <AppShell><div className="ff-page">
    <PageHeading eyebrow="PROOFOS DELIVERY WORKSPACE" title="Projects & milestones" action={<Button variant="primary" onClick={() => setShowForm(!showForm)}><Plus size={16} />New project</Button>}>
      Turn approved requirements and delivery evidence into a reviewable acceptance package.
    </PageHeading>
    <ActionFeedback action={action} />
    {showForm && <ProofSection title="Create a project" description="The authenticated project owner controls scope. Customer acceptance requires the configured customer account.">
      <form onSubmit={submit} className="proof-form"><fieldset disabled={action.busy} className="proof-form-grid">
        <label>Project name<input required maxLength={200} className={inputClass} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        <label>Customer name<input required maxLength={200} className={inputClass} value={form.customer_name} onChange={(event) => setForm({ ...form, customer_name: event.target.value })} /></label>
        <label className="proof-wide">Description<textarea rows={3} maxLength={4000} className={inputClass} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
        <label className="proof-wide">Customer approver account ID (optional)<input pattern="user_[A-Za-z0-9_-]+" maxLength={195} className={inputClass} value={form.customer_approver_id ?? ""} placeholder="Authenticated customer's user_… account ID" onChange={(event) => setForm({ ...form, customer_approver_id: event.target.value || null })} /><span className="mt-1 block text-xs text-muted">Use the customer&apos;s authenticated account ID. Approval cannot be delegated through an email address or a shared link.</span></label>
        <RepositorySelection accounts={accounts} value={form} change={setForm} />
        <label>Branch<input required maxLength={200} className={inputClass} value={form.branch} onChange={(event) => setForm({ ...form, branch: event.target.value })} /></label>
        <div className="proof-wide proof-actions"><Button type="submit" variant="primary" loading={action.busy}>Create project</Button><Button type="button" onClick={() => setShowForm(false)}>Cancel</Button></div>
      </fieldset></form>
      {connectors.error && <p className="mt-3 text-xs text-warning">Could not load connections. A project can still be created without a repository.</p>}
    </ProofSection>}
    {projects.loading && <p role="status" className="my-6 text-sm text-muted">Loading projects…</p>}
    {projects.error && <ErrorState error={projects.error} retry={projects.reload} />}
    {projects.data && <ProofSection title="Your projects" description="Verification, customer acceptance and invoice eligibility are recorded separately.">
      {!projects.data.length && <EmptyState>No projects yet. Create a project, then upload an SRS or contract through Sources.</EmptyState>}
      <div className="proof-project-grid">{projects.data.map((project) => <Link key={project.id} href={`/projects/${encodeURIComponent(project.id)}`} className="proof-project-card"><FolderKanban size={22} className="text-accent" /><h3>{project.name}</h3><p>{project.customer_name}</p><p className="proof-description">{project.description || "No project description supplied."}</p><span className="text-xs text-muted">{project.repository_name ?? "Repository not associated"} · {formatDate(project.created_at)}</span><span className="mt-4 block text-sm text-accent">Open project →</span></Link>)}</div>
    </ProofSection>}
  </div></AppShell>;
}
