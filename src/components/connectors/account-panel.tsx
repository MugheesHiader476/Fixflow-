"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { disconnectConnector, syncConnector } from "@/lib/connectors";
import type { ConnectorAccount } from "@/lib/connector-contracts";
import { ResourceSelection } from "./resource-selection";

function DisconnectConfirmation({ account, busy, onClose, onConfirm }: {
  account: ConnectorAccount; busy: boolean; onClose: () => void; onConfirm: (policy: "retain" | "soft_delete" | "purge") => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [policy, setPolicy] = useState<"retain" | "soft_delete" | "purge">("soft_delete");
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} onCancel={onClose} aria-labelledby="disconnect-title" className="m-auto max-w-lg rounded-2xl border border-border bg-panel p-6 text-foreground backdrop:bg-black/60">
    <h2 id="disconnect-title" className="text-xl font-semibold">Disconnect {account.display_name}?</h2>
    <p className="my-3 text-sm text-muted">Synchronization stops and connected content becomes unavailable to retrieval with every option.</p>
    <fieldset disabled={busy} className="space-y-3">
      <label className="block"><input type="radio" name="policy" checked={policy === "retain"} onChange={() => setPolicy("retain")} /> Retain content for an authorized reconnect</label>
      <label className="block"><input type="radio" name="policy" checked={policy === "soft_delete"} onChange={() => setPolicy("soft_delete")} /> Mark content removed</label>
      <label className="block"><input type="radio" name="policy" checked={policy === "purge"} onChange={() => setPolicy("purge")} /> Permanently purge connected sources and chunks</label>
    </fieldset>
    {policy === "purge" && <p className="mt-3 text-danger">Purging cannot be undone.</p>}
    <div className="mt-5 flex gap-3"><Button disabled={busy} onClick={onClose}>Cancel</Button><Button variant="danger" loading={busy} onClick={() => onConfirm(policy)}>Confirm disconnect</Button></div>
  </dialog>;
}

export function AccountPanel({ account, onChanged, onReconnect }: {
  account: ConnectorAccount; onChanged: () => void; onReconnect: () => void;
}) {
  const [selecting, setSelecting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const authorized = ["connected", "syncing", "connected_with_warning"].includes(account.status);
  const syncing = ["pending", "running", "waiting"].includes(account.sync_status);

  async function run(action: () => Promise<ConnectorAccount>) {
    setBusy(true); setError(null);
    try { await action(); setConfirming(false); onChanged(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Connector operation failed."); }
    finally { setBusy(false); }
  }

  return <div className="mt-5 border-t border-border pt-5">
    <h3 className="break-words font-medium">{account.display_name}</h3>
    <dl className="mt-2 grid grid-cols-2 gap-2 text-sm text-muted">
      <dt>Connection</dt><dd>{account.status.replaceAll("_", " ")}</dd>
      <dt>Authentication</dt><dd>{account.authentication_status}</dd>
      <dt>Provider</dt><dd>{account.provider_health.replaceAll("_", " ")}</dd>
      <dt>Sync</dt><dd aria-live="polite">{account.sync_status.replaceAll("_", " ")}</dd>
      <dt>Last successful sync</dt><dd>{account.last_sync_at ? new Date(account.last_sync_at).toLocaleString() : "Not yet synchronized"}</dd>
      <dt>Selected resources</dt><dd>{account.configuration.resource_ids.length}</dd>
    </dl>
    {!!Object.keys(account.progress).length && <p className="mt-3 text-xs text-muted">Discovered: {account.progress.discovered ?? 0} · Queued for ingestion: {account.progress.queued ?? 0} · Unchanged: {account.progress.unchanged ?? 0} · Removed: {account.progress.removed ?? 0} · Failed: {account.progress.failed ?? 0}</p>}
    {(error || account.error_message) && <p role="alert" className="mt-3 text-sm text-danger">{error || account.error_message}</p>}
    <div className="mt-4 flex flex-wrap gap-2">
      {authorized && <>
        <Button size="sm" disabled={busy || syncing} onClick={() => setSelecting(true)}>Configure resources</Button>
        <Button size="sm" variant="primary" loading={busy} disabled={syncing || !account.configuration.resource_ids.length} onClick={() => void run(() => syncConnector(account.id))}>Sync now</Button>
        <Button size="sm" disabled={busy || syncing || !account.configuration.resource_ids.length} onClick={() => void run(() => syncConnector(account.id, "reconcile"))}>Reconcile</Button>
      </>}
      {!authorized && <Button size="sm" onClick={onReconnect}>Reconnect</Button>}
      {account.status !== "disconnected" && <Button size="sm" variant="danger" disabled={busy} onClick={() => setConfirming(true)}>Disconnect</Button>}
    </div>
    <p className="mt-3 text-xs text-muted">Sync completion means resources were fetched and queued. Check parsing and chunk status in <Link href="/sources" className="underline">Knowledge Sources</Link>.</p>
    {selecting && <ResourceSelection account={account} onClose={() => setSelecting(false)} onSaved={() => { setSelecting(false); onChanged(); }} />}
    {confirming && <DisconnectConfirmation account={account} busy={busy} onClose={() => setConfirming(false)} onConfirm={(policy) => void run(() => disconnectConnector(account.id, policy))} />}
  </div>;
}
