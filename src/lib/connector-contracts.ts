export const PROVIDERS = ["gmail", "github", "google_drive", "slack"] as const;
export type ConnectorProvider = typeof PROVIDERS[number];
export type GitHubCategory = "code" | "issues" | "pull_requests" | "commits" | "releases";
export interface ConnectorSelection {
  resource_ids: string[];
  branches: Record<string, string>;
  categories: GitHubCategory[];
  start_date: string | null;
  end_date: string | null;
  attachments: boolean;
  threads: boolean;
  files: boolean;
  mime_types: string[];
}
export interface ConnectorAccount {
  id: string;
  provider: ConnectorProvider;
  display_name: string;
  external_account_id: string;
  status: "connected" | "syncing" | "connected_with_warning" | "reauth_required" | "revoked" | "error" | "disconnected";
  authentication_status: string;
  provider_health: string;
  sync_status: "idle" | "pending" | "running" | "waiting" | "complete" | "complete_with_warning" | "failed" | "cancelled";
  last_successful_request: string | null;
  last_sync_at: string | null;
  error_message: string | null;
  configuration: ConnectorSelection;
  progress: Record<string, number>;
  created_at: string;
}
export interface ConnectorCard {
  provider: ConnectorProvider;
  configured: boolean;
  setup_message: string | null;
  accounts: ConnectorAccount[];
  install_url: string | null;
}
export interface ConnectorResource {
  id: string;
  name: string;
  kind: string;
  parent_id: string | null;
  version: string | null;
  metadata: Record<string, unknown>;
}
export interface ConnectorResourcePage { resources: ConnectorResource[]; next_cursor: string | null }
export interface ConnectorQuery {
  operation: "list" | "count";
  resource_id?: string;
  sender?: string;
  recipient?: string;
  start_date?: string;
  end_date?: string;
  limit?: number;
  cursor?: string;
}
export interface ConnectorQueryResult {
  resources: ConnectorResource[];
  count: number;
  exact: boolean;
  next_cursor: string | null;
}

const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string";
const optional = (v: unknown) => v === null || text(v);
const strings = (v: unknown) => Array.isArray(v) && v.every(text);
const provider = (v: unknown) => text(v) && PROVIDERS.some((p) => p === v);
const resources = (v: unknown) => Array.isArray(v) && v.every((r) => record(r)
  && ["id", "name", "kind"].every((k) => text(r[k])) && optional(r.parent_id) && optional(r.version) && record(r.metadata));

export function validSelection(v: unknown): boolean {
  return record(v) && strings(v.resource_ids) && record(v.branches) && Object.values(v.branches).every(text)
    && Array.isArray(v.categories) && v.categories.every((c) => ["code", "issues", "pull_requests", "commits", "releases"].includes(String(c)))
    && optional(v.start_date) && optional(v.end_date) && strings(v.mime_types)
    && ["attachments", "threads", "files"].every((k) => typeof v[k] === "boolean");
}

export function validAccount(v: unknown): boolean {
  return record(v) && ["id", "display_name", "external_account_id", "authentication_status", "provider_health", "created_at"].every((k) => text(v[k]))
    && provider(v.provider) && ["connected", "syncing", "connected_with_warning", "reauth_required", "revoked", "error", "disconnected"].includes(String(v.status))
    && ["idle", "pending", "running", "waiting", "complete", "complete_with_warning", "failed", "cancelled"].includes(String(v.sync_status))
    && ["last_successful_request", "last_sync_at", "error_message"].every((k) => optional(v[k])) && validSelection(v.configuration)
    && record(v.progress) && Object.values(v.progress).every((n) => typeof n === "number" && Number.isSafeInteger(n) && n >= 0);
}

export function authorizationUrl(value: unknown): value is string {
  if (!text(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.hash && !url.port
      && ((url.hostname === "accounts.google.com" && url.pathname === "/o/oauth2/v2/auth")
        || (url.hostname === "github.com" && url.pathname === "/login/oauth/authorize")
        || (url.hostname === "slack.com" && url.pathname === "/oauth/v2/authorize"));
  } catch { return false; }
}

export function validConnectorResponse(path: string, value: unknown): boolean {
  const route = path.split("?")[0];
  if (route === "/api/connectors") return Array.isArray(value) && value.every((v) => record(v) && provider(v.provider)
    && typeof v.configured === "boolean" && optional(v.setup_message)
    && (v.install_url === null || (text(v.install_url) && /^https:\/\/github\.com\/apps\/[a-zA-Z0-9-]+\/installations\/new$/.test(v.install_url)))
    && Array.isArray(v.accounts) && v.accounts.every(validAccount));
  if (route.endsWith("/connect")) return record(value) && authorizationUrl(value.authorization_url);
  if (route.endsWith("/resources")) return record(value) && optional(value.next_cursor) && resources(value.resources);
  if (route.endsWith("/query")) return record(value) && optional(value.next_cursor) && resources(value.resources)
    && typeof value.count === "number" && Number.isSafeInteger(value.count) && value.count >= 0 && typeof value.exact === "boolean";
  return validAccount(value);
}
