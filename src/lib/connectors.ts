import { apiFetch } from "./api";
import type { ConnectorAccount, ConnectorCard, ConnectorProvider, ConnectorQuery, ConnectorQueryResult, ConnectorResourcePage, ConnectorSelection } from "./connector-contracts";

export function listConnectors(signal?: AbortSignal): Promise<ConnectorCard[]> {
  return apiFetch("/api/connectors", { signal });
}
export function connectProvider(provider: ConnectorProvider): Promise<{ authorization_url: string }> {
  return apiFetch(`/api/connectors/${provider}/connect`, { method: "POST" });
}
export function discoverResources(id: string, cursor?: string, signal?: AbortSignal): Promise<ConnectorResourcePage> {
  const query = cursor ? "?cursor=" + encodeURIComponent(cursor) : "";
  return apiFetch(`/api/connectors/${id}/resources${query}`, { signal });
}
export function configureConnector(id: string, selection: ConnectorSelection): Promise<ConnectorAccount> {
  return apiFetch(`/api/connectors/${id}/configure`, { method: "POST", body: JSON.stringify(selection) });
}
export function syncConnector(id: string, mode: "incremental" | "reconcile" = "incremental"): Promise<ConnectorAccount> {
  return apiFetch(`/api/connectors/${id}/sync`, { method: "POST", body: JSON.stringify({ mode }) });
}
export function disconnectConnector(id: string, policy: "retain" | "soft_delete" | "purge"): Promise<ConnectorAccount> {
  return apiFetch(`/api/connectors/${id}/disconnect`, { method: "POST", body: JSON.stringify({ policy }) });
}
export function connectorStatus(id: string, signal?: AbortSignal): Promise<ConnectorAccount> {
  return apiFetch(`/api/connectors/${id}/status`, { signal });
}
export function checkConnector(id: string): Promise<ConnectorAccount> {
  return apiFetch(`/api/connectors/${id}/health`, { method: "POST" });
}
export function queryConnector(id: string, query: ConnectorQuery, signal?: AbortSignal): Promise<ConnectorQueryResult> {
  return apiFetch(`/api/connectors/${id}/query`, { method: "POST", body: JSON.stringify(query), signal });
}
