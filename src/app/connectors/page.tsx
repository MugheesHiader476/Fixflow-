"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeading } from "@/components/layout/page-heading";
import { AccountPanel } from "@/components/connectors/account-panel";
import { Search, ShieldCheck, RefreshCw } from "lucide-react";
import { KnowledgeVisual } from "@/components/visuals/knowledge-visual";
import { ProviderIcon } from "@/components/connectors/provider-icon";
import { Button } from "@/components/ui/button";
import { connectProvider, listConnectors } from "@/lib/connectors";
import type { ConnectorProvider } from "@/lib/connector-contracts";
import { useResource } from "@/lib/use-resource";

const NAMES = { gmail: "Gmail", github: "GitHub", google_drive: "Google Drive", slack: "Slack" };
const DETAIL = { gmail: "Selected labels, messages and optional attachments.", github: "Installed repositories, files, issues and pull requests.", google_drive: "Selected files, folders and shared drives.", slack: "Authorized channels, messages and thread replies." };

function accountLabel(count: number) {
  if (!count) return "Not connected";
  return count + (count === 1 ? " linked account" : " linked accounts");
}

function subscribeLocation(callback: () => void) { window.addEventListener("popstate", callback); return () => window.removeEventListener("popstate", callback); }
function connectionNotice() {
  const query = new URL(window.location.href).searchParams;
  if (query.has("connection_error")) return "Authorization did not complete. Start connection again and check provider permissions.";
  return query.has("connected") ? "Account connected. Select resources to start synchronization." : null;
}

export default function ConnectorsPage() {
  const { data, loading, error, reload } = useResource(listConnectors);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "connected">("all");
  const [busy, setBusy] = useState<ConnectorProvider | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const notice = useSyncExternalStore(subscribeLocation, connectionNotice, () => null);
  const active = data?.some((card) => card.accounts.some((a) => ["pending", "running", "waiting"].includes(a.sync_status))) ?? false;
  const connected = data?.some((card) => card.accounts.some((a) => ["connected", "syncing", "connected_with_warning"].includes(a.status))) ?? false;
  useEffect(() => {
    if (!connected) return;
    const timer = setInterval(reload, active ? 5000 : 15000);
    return () => clearInterval(timer);
  }, [active, connected, reload]);

  async function connect(provider: ConnectorProvider) {
    setBusy(provider); setActionError(null);
    try { const result = await connectProvider(provider); window.location.assign(result.authorization_url); }
    catch (failure) { setActionError(failure instanceof Error ? failure.message : "Could not start authorization."); setBusy(null); }
  }

  const visible = data?.filter((card) => NAMES[card.provider].toLowerCase().includes(query.trim().toLowerCase())
    && (filter === "all" || card.accounts.some((account) => ["connected", "syncing", "connected_with_warning"].includes(account.status))));

  return <AppShell sessionTitle="Connected Apps"><div className="ff-page ff-connectors-page">
    <PageHeading eyebrow="MAKE IT YOURS" title="Connected Apps" action={<Button onClick={reload}><RefreshCw size={14} />Refresh connections</Button>}>Your tools, a little closer. Choose what to connect and exactly what to bring into your workspace.</PageHeading>
    <div className="ff-connector-controls">
      <div className="ff-filter-group" aria-label="Filter connectors"><button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All apps</button><button type="button" aria-pressed={filter === "connected"} onClick={() => setFilter("connected")}>Connected</button></div>
      <label className="ff-connector-search"><Search size={16} /><input aria-label="Search connectors" placeholder="Search apps…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
    </div>
    <section className="ff-connections-banner" aria-label="Private connections"><div><p><ShieldCheck size={13} />CONNECTED, ON YOUR TERMS</p><h2>Good work starts with good context.</h2><span>Bring selected content from the tools you already use.<br />You choose the sources. You stay in control.</span></div><KnowledgeVisual /></section>
    <div className="ff-connector-section-title"><h2>Your apps</h2><span>{data ? visible?.length : "…"} available to view</span></div>
    {notice && <p role="status" className="my-4 text-sm text-muted">{notice}</p>}
    {(error || actionError) && <p role="alert" className="my-4 text-danger">{error || actionError}</p>}
    {loading && !data && <p role="status">Loading connections…</p>}
    {data && visible?.length === 0 && <p role="status" className="ff-connector-empty">No apps match this view. Try another search or choose All apps.</p>}
    {data && <div className="ff-connector-grid">{visible?.map((card) => <section key={card.provider} aria-label={NAMES[card.provider]} className="ff-connector-card">
      <div className="ff-connector-card-title"><ProviderIcon provider={card.provider} /><div><h3>{NAMES[card.provider]}</h3><span>{accountLabel(card.accounts.length)}</span></div><span className={card.configured ? "ff-connection-tag" : "ff-connection-tag ff-needs-setup"}>{card.configured ? "Available" : "Setup needed"}</span></div><p className="ff-connector-detail">{DETAIL[card.provider]}</p>
      {card.gmail_send_enabled && <p className="mt-3 text-xs text-muted">Gmail sending is enabled on this server. Connecting also requests send permission; reconnect existing accounts to grant it. Workflow recipient authorization is still required.</p>}
      {!card.configured && <p className="mt-3 text-sm text-muted">{card.setup_message}</p>}
      {!card.accounts.length && <p className="mt-3 text-sm text-muted">No account connected.</p>}
      <div className="mt-4 flex items-center gap-3"><Button variant="primary" disabled={!card.configured || busy !== null} loading={busy === card.provider} onClick={() => void connect(card.provider)}>Connect {NAMES[card.provider]}</Button>
        {card.install_url && <a href={card.install_url} target="_blank" rel="noopener noreferrer" className="text-sm underline">Install GitHub App</a>}
      </div>
      {card.accounts.map((account) => <AccountPanel key={account.id} account={account} onChanged={reload} onReconnect={() => void connect(card.provider)} />)}
    </section>)}</div>}
  </div></AppShell>;
}
