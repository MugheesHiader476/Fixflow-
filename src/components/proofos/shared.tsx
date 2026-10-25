"use client";

import { useRef, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export const inputClass = "mt-1.5 w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground";

export function StatusBadge({ status }: { status: string }) {
  let tone: "success" | "danger" | "warning" | "muted" = "muted";
  if (["PASSED", "APPROVED", "approved", "accepted", "completed"].includes(status)) tone = "success";
  if (["FAILED", "failed", "rejected", "BLOCKED"].includes(status)) tone = "danger";
  if (["pending", "NEEDS_REVIEW", "UNVERIFIED", "queued", "running", "rework_requested"].includes(status)) tone = "warning";
  return <Badge tone={tone}>{status.replaceAll("_", " ")}</Badge>;
}
export function ProofSection({ title, children, description }: { title: string; children: ReactNode; description?: string }) {
  return <section className="proof-section"><h2>{title}</h2>{description && <p className="proof-description">{description}</p>}{children}</section>;
}
export function EmptyState({ children }: { children: ReactNode }) { return <p className="proof-empty">{children}</p>; }
export function ErrorState({ error, retry }: { error: string; retry?: () => void }) {
  return <div role="alert" className="my-4 rounded-xl border border-danger/40 bg-danger/10 p-4 text-sm text-danger"><p className="break-words">{error}</p>{retry && <Button className="mt-3" onClick={retry}>Retry</Button>}</div>;
}
export function useProofAction(refresh?: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const pending = useRef(false);
  async function perform(action: () => Promise<unknown>, success: string): Promise<boolean> {
    if (pending.current) return false;
    pending.current = true; setBusy(true); setError(null); setMessage(null);
    try { await action(); setMessage(success); refresh?.(); return true; }
    catch (failure) { setError(failure instanceof Error ? failure.message : "The request failed. Try again."); return false; }
    finally { pending.current = false; setBusy(false); }
  }
  return { busy, error, message, perform };
}
export function ActionFeedback({ action }: { action: { error: string | null; message: string | null } }) {
  return <>{action.error && <ErrorState error={action.error} />}{action.message && <p role="status" className="my-3 break-words text-sm text-muted">{action.message}</p>}</>;
}
export function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown date" : date.toLocaleString();
}
export function money(amount: number, currency: string): string {
  try {
    const formatter = new Intl.NumberFormat(undefined, { style: "currency", currency });
    const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
    return formatter.format(amount / 10 ** digits);
  }
  catch { return `${amount} minor units ${currency}`; }
}
