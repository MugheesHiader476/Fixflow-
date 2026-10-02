"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeading } from "@/components/layout/page-heading";
import { AccountPanel } from "@/components/connectors/account-panel";
import { Button } from "@/components/ui/button";
import { connectProvider, listConnectors } from "@/lib/connectors";
import type { ConnectorProvider } from "@/lib/connector-contracts";
import { useResource } from "@/lib/use-resource";

const NAMES = { gmail: "Gmail", github: "GitHub", google_drive: "Google Drive", slack: "Slack" };
const DETAIL = { gmail: "Selected labels, messages and optional attachments.", github: "Installed repositories, files, issues and pull requests.", google_drive: "Selected files, folders and shared drives.", slack: "Authorized channels, messages and thread replies." };

function subscribeLocation(callback: () => void) { window.addEventListener("popstate", callback); return () => window.removeEventListener("popstate", callback); }
function connectionNotice() {
  const query = new URL(window.location.href).searchParams;
  if (query.has("connection_error")) return "Authorization did not complete. Start connection again and check provider permissions.";
  return query.has("connected") ? "Account connected. Select resources to start synchronization." : null;
}

export default function ConnectorsPage() {
  const { data, loading, error, reload } = useResource(listConnectors);
  const [busy, setBusy] = useState<ConnectorProvider | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const notice = useSyncExternalStore(subscribeLocation, connectionNotice, () => null);
  const active = data?.some((card) => card.accounts.some((a) => ["pending", "running", "waiting"].includes(a.sync_status))) ?? false;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [active, reload]);

  async function connect(provider: ConnectorProvider) {
    setBusy(provider); setActionError(null);
    try { const result = await connectProvider(provider); window.location.assign(result.authorization_url); }
    catch (failure) { setActionError(failure instanceof Error ? failure.message : "Could not start authorization."); setBusy(null); }
  }

  return <AppShell sessionTitle="Connected Apps" techs={["Documentation"]}>
    <PageHeading eyebrow="YOUR KNOWLEDGE" title="Connected Apps" action={<Button onClick={reload}>Refresh connections</Button>}>Connect an account, select authorized resources, and synchronize them into your knowledge sources.</PageHeading>
    {notice && <p role="status" className="my-4 text-sm text-muted">{notice}</p>}
    {(error || actionError) && <p role="alert" className="my-4 text-danger">{error || actionError}</p>}
    {loading && !data && <p role="status">Loading connections…</p>}
    {data && <div className="grid gap-5 md:grid-cols-2">{data.map((card) => <section key={card.provider} aria-label={NAMES[card.provider]} className="rounded-2xl border border-border bg-panel p-6">
      <h2 className="text-xl font-semibold">{NAMES[card.provider]}</h2><p className="mt-2 text-sm text-muted">{DETAIL[card.provider]}</p>
      {!card.configured && <p className="mt-3 text-sm text-muted">{card.setup_message}</p>}
      {!card.accounts.length && <p className="mt-3 text-sm text-muted">No account connected.</p>}
      <div className="mt-4 flex items-center gap-3"><Button variant="primary" disabled={!card.configured || busy !== null} loading={busy === card.provider} onClick={() => void connect(card.provider)}>Connect {NAMES[card.provider]}</Button>
        {card.install_url && <a href={card.install_url} target="_blank" rel="noopener noreferrer" className="text-sm underline">Install GitHub App</a>}
      </div>
      {card.accounts.map((account) => <AccountPanel key={account.id} account={account} onChanged={reload} onReconnect={() => void connect(card.provider)} />)}
    </section>)}</div>}
  </AppShell>;
}
