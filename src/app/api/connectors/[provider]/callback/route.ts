import { NextResponse } from "next/server";
import { PROVIDERS } from "@/lib/connector-contracts";
import { proxyBackend } from "@/lib/server/backend-proxy";
import { applicationOrigin } from "@/lib/server/application-origin";

const FAILURES = new Set(["INVALID_STATE", "REAUTH_REQUIRED", "MISSING_SCOPE", "NOT_CONFIGURED", "PROVIDER_ERROR", "PROVIDER_UNAVAILABLE", "RATE_LIMITED", "INACCESSIBLE", "API_DISABLED"]);

async function failureCode(response: Response): Promise<string> {
  if (response.status === 401) return "sign_in";
  try {
    const payload: unknown = await response.json();
    if (typeof payload === "object" && payload !== null && "error" in payload) {
      const error = payload.error;
      if (typeof error === "object" && error !== null && "code" in error && typeof error.code === "string" && FAILURES.has(error.code)) return error.code.toLowerCase();
    }
  } catch { /* An upstream proxy may return a non-JSON failure; use the fixed fallback. */ }
  return "authorization";
}

export async function GET(request: Request, context: { params: Promise<{ provider: string }> }) {
  const { provider } = await context.params;
  const origin = applicationOrigin(request);
  if (!origin) return NextResponse.json({ error: "Public application URL is not configured correctly" }, { status: 503 });
  const redirect = new URL("/connectors", origin);
  const input = new URL(request.url).searchParams;
  if (!PROVIDERS.some((p) => p === provider) || input.has("error") || input.getAll("code").length !== 1 || input.getAll("state").length !== 1) {
    redirect.searchParams.set("connection_error", "authorization");
  } else {
    const target = new URL(request.url);
    target.search = new URLSearchParams({ state: input.get("state") ?? "", code: input.get("code") ?? "" }).toString();
    const result = await proxyBackend(new Request(target, { headers: request.headers }), `api/connectors/${provider}/callback`);
    redirect.searchParams.set(result.ok ? "connected" : "connection_error", result.ok ? provider : await failureCode(result));
  }
  const result = NextResponse.redirect(redirect, 303);
  result.headers.set("Cache-Control", "no-store");
  result.headers.set("Referrer-Policy", "no-referrer");
  return result;
}
