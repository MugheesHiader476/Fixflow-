import { NextResponse } from "next/server";
import { PROVIDERS } from "@/lib/connector-contracts";
import { proxyBackend } from "@/lib/server/backend-proxy";

export async function GET(request: Request, context: { params: Promise<{ provider: string }> }) {
  const { provider } = await context.params;
  const redirect = new URL("/connectors", request.url);
  const input = new URL(request.url).searchParams;
  if (!PROVIDERS.some((p) => p === provider) || input.has("error") || input.getAll("code").length !== 1 || input.getAll("state").length !== 1) {
    redirect.searchParams.set("connection_error", "authorization");
  } else {
    const target = new URL(request.url);
    target.search = new URLSearchParams({ state: input.get("state") ?? "", code: input.get("code") ?? "" }).toString();
    const result = await proxyBackend(new Request(target, { headers: request.headers }), `api/connectors/${provider}/callback`);
    redirect.searchParams.set(result.ok ? "connected" : "connection_error", provider);
  }
  const result = NextResponse.redirect(redirect, 303);
  result.headers.set("Cache-Control", "no-store");
  result.headers.set("Referrer-Policy", "no-referrer");
  return result;
}
