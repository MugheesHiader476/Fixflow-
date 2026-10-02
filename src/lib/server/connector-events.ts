import { PROVIDERS } from "@/lib/connector-contracts";

const SIGNATURE_HEADERS = ["authorization", "x-hub-signature-256", "x-github-delivery", "x-github-event", "x-slack-signature", "x-slack-request-timestamp", "x-goog-channel-id", "x-goog-channel-token", "x-goog-resource-id", "x-goog-resource-state", "x-goog-message-number"];

function eventHeaders(request: Request): Headers {
  const headers = new Headers({ "Content-Type": "application/json" });
  for (const name of SIGNATURE_HEADERS) { const value = request.headers.get(name); if (value) headers.set(name, value); }
  return headers;
}

export async function proxyConnectorEvent(request: Request, provider: string): Promise<Response> {
  if (!PROVIDERS.some((p) => p === provider)) return Response.json({ error: "Unknown provider" }, { status: 404 });
  const apiUrl = process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) return Response.json({ error: "Backend connection is not configured" }, { status: 503 });
  const maximum = 1024 * 1024;
  const parts: Uint8Array[] = [];
  let size = 0;
  const reader = request.body?.getReader();
  try {
    if (reader) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maximum) { await reader.cancel(); return Response.json({ error: "Event exceeds limit" }, { status: 413 }); }
        parts.push(value);
      }
    }
    const body = new Uint8Array(size);
    let offset = 0;
    for (const part of parts) { body.set(part, offset); offset += part.byteLength; }
    const headers = eventHeaders(request);
    const result = await fetch(`${apiUrl.replace(/\/$/, "")}/webhooks/connectors/${provider}`, {
      method: "POST", headers, body, cache: "no-store", redirect: "error",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(15_000)]),
    });
    return new Response(result.body, { status: result.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch { return Response.json({ error: "Event delivery unavailable" }, { status: 502 }); }
  finally { reader?.releaseLock(); }
}
