"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { configureConnector, discoverResources } from "@/lib/connectors";
import type { ConnectorAccount, ConnectorResource, GitHubCategory } from "@/lib/connector-contracts";

const CATEGORIES: GitHubCategory[] = ["code", "issues", "pull_requests", "commits", "releases"];

export function ResourceSelection({ account, onSaved, onClose }: {
  account: ConnectorAccount; onSaved: () => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [selection, setSelection] = useState(account.configuration);
  const [mimeTypes, setMimeTypes] = useState(account.configuration.mime_types.join(", "));
  const [resources, setResources] = useState<ConnectorResource[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialog.current?.showModal();
    const abort = new AbortController();
    controller.current = abort;
    void discoverResources(account.id, undefined, abort.signal).then((page) => {
      if (!abort.signal.aborted) { setResources(page.resources); setCursor(page.next_cursor); }
    }).catch((failure: unknown) => {
      if (!abort.signal.aborted) setError(failure instanceof Error ? failure.message : "Resource discovery failed.");
    }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [account.id]);

  async function more() {
    if (!cursor) return;
    setBusy(true); setError(null);
    try {
      const page = await discoverResources(account.id, cursor, controller.current?.signal);
      setResources((items) => [...new Map([...items, ...page.resources].map((r) => [r.id, r])).values()]);
      setCursor(page.next_cursor);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Resource discovery failed."); }
    finally { setBusy(false); }
  }

  function toggle(id: string, checked: boolean) {
    setSelection((current) => {
      const branches = { ...current.branches };
      if (!checked) delete branches[id];
      return { ...current, branches, resource_ids: checked ? [...current.resource_ids, id] : current.resource_ids.filter((v) => v !== id) };
    });
  }

  function toggleCategory(category: GitHubCategory, checked: boolean) {
    setSelection((current) => ({ ...current, categories: checked ? [...current.categories, category] : current.categories.filter((value) => value !== category) }));
  }

  async function save() {
    setBusy(true); setError(null);
    try { await configureConnector(account.id, { ...selection, mime_types: mimeTypes.split(",").map((value) => value.trim()).filter(Boolean) }); onSaved(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Could not save selection."); }
    finally { setBusy(false); }
  }

  return <dialog ref={dialog} onCancel={onClose} className="m-auto max-h-[85vh] w-full max-w-2xl overflow-auto rounded-2xl border border-border bg-panel p-6 text-foreground backdrop:bg-black/60" aria-labelledby="resource-title">
    <h2 id="resource-title" className="text-xl font-semibold">Select resources for {account.display_name}</h2>
    <p className="mt-2 text-sm text-muted">Only resources authorized by your provider account can be synchronized. Changing selection disables existing content until it is synchronized again.</p>
    {error && <p role="alert" className="mt-3 text-danger">{error}</p>}
    <fieldset disabled={busy} className="my-5 space-y-3">
      <legend className="mb-2 font-medium">Authorized resources ({selection.resource_ids.length}/200 selected)</legend>
      {selection.resource_ids.filter((id) => !resources.some((r) => r.id === id)).map((id) => <label key={id} className="flex gap-2"><input type="checkbox" checked onChange={() => toggle(id, false)} />Saved selection: {id}</label>)}
      {resources.map((resource) => <div key={resource.id}>
        <label className="flex gap-2"><input type="checkbox" checked={selection.resource_ids.includes(resource.id)} disabled={!selection.resource_ids.includes(resource.id) && selection.resource_ids.length >= 200} onChange={(event) => toggle(resource.id, event.target.checked)} /><span>{resource.name} <small className="text-muted">({resource.kind.replaceAll("_", " ")})</small></span></label>
        {account.provider === "github" && selection.resource_ids.includes(resource.id) && <label className="mt-2 block pl-6 text-sm">Branch for {resource.name}<input className="ml-2 rounded border border-border bg-panel-2 p-2" placeholder="Repository default" value={selection.branches[resource.id] ?? ""} maxLength={255} onChange={(event) => setSelection((current) => {
          const branches = { ...current.branches };
          if (event.target.value) branches[resource.id] = event.target.value; else delete branches[resource.id];
          return { ...current, branches };
        })} /></label>}
      </div>)}
      {!resources.length && !busy && !error && <p>No authorized resources were returned. Check provider permissions or installation.</p>}
    </fieldset>
    {cursor && <Button loading={busy} onClick={() => void more()}>Load more resources</Button>}
    <fieldset disabled={busy} className="my-5 flex flex-wrap gap-4">
      <legend className="mb-2 font-medium">Sync options</legend>
      {(account.provider === "gmail" || account.provider === "slack") && <>
        <label className="text-sm">From date (UTC)<input type="date" className="ml-2 rounded border border-border bg-panel-2 p-2" value={selection.start_date ?? ""} onChange={(e) => setSelection((s) => ({ ...s, start_date: e.target.value || null }))} /></label>
        <label className="text-sm">Through date (UTC)<input type="date" min={selection.start_date ?? undefined} className="ml-2 rounded border border-border bg-panel-2 p-2" value={selection.end_date ?? ""} onChange={(e) => setSelection((s) => ({ ...s, end_date: e.target.value || null }))} /></label>
      </>}
      {account.provider === "gmail" && <label><input type="checkbox" checked={selection.attachments} onChange={(e) => setSelection((s) => ({ ...s, attachments: e.target.checked }))} /> Include attachments</label>}
      {account.provider === "slack" && <>
        <label><input type="checkbox" checked={selection.threads} onChange={(e) => setSelection((s) => ({ ...s, threads: e.target.checked }))} /> Include thread replies</label>
        <label><input type="checkbox" checked={selection.files} onChange={(e) => setSelection((s) => ({ ...s, files: e.target.checked }))} /> Download supported files</label>
        <p className="text-xs text-muted">File metadata is preserved with messages. Slack history limits can make large syncs take time.</p>
      </>}
      {account.provider === "github" && CATEGORIES.map((category) => <label key={category}><input type="checkbox" checked={selection.categories.includes(category)} onChange={(e) => toggleCategory(category, e.target.checked)} /> {category.replaceAll("_", " ")}</label>)}
      {account.provider === "google_drive" && <label className="w-full text-sm">File MIME types (optional, comma separated)<input className="mt-2 w-full rounded border border-border bg-panel-2 p-2" placeholder="application/pdf, text/plain" value={mimeTypes} onChange={(event) => setMimeTypes(event.target.value)} /></label>}
    </fieldset>
    <div className="flex justify-end gap-3"><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!!(selection.start_date && selection.end_date && selection.start_date > selection.end_date)} onClick={() => void save()}>Save selection</Button></div>
  </dialog>;
}
