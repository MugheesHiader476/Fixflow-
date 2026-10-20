import { NextResponse } from "next/server";
import { PROVIDERS } from "@/lib/connector-contracts";
import { proxyBackend } from "@/lib/server/backend-proxy";
import { applicationOrigin } from "@/lib/server/application-origin";
import { knownOAuthFailure, providerAuthorizationFailure } from "@/lib/oauth-errors";
import { oauthRedirect, oauthSignInRedirect } from "@/lib/server/oauth-redirect";

async function failureCode(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (typeof payload === "object" && payload !== null && "error" in payload) {
      const error = payload.error;
      if (response.status === 401 && error === "Sign in to use FixFlow.") return "sign_in";
      if (typeof error === "object" && error !== null && "code" in error && typeof error.code === "string") {
        const failure = knownOAuthFailure(error.code);
        if (failure) return failure;
      }
    }
  } catch { /* An upstream proxy may return a non-JSON failure; use the fixed fallback. */ }
  return response.status === 401 ? "not_configured" : "authorization";
}

export async function GET(request: Request, context: { params: Promise<{ provider: string }> }) {
  const { provider } = await context.params;
  const origin = applicationOrigin(request);
  if (!origin) return NextResponse.json({ error: "Public application URL is not configured correctly" }, { status: 503 });
  const redirect = new URL("/connectors", origin);
  const input = new URL(request.url).searchParams;
  if (!PROVIDERS.some((p) => p === provider)) {
    redirect.searchParams.set("connection_error", "authorization");
  } else if (input.has("error")) {
    redirect.searchParams.set("connection_error", input.getAll("error").length === 1 ? providerAuthorizationFailure(input.get("error")) : "authorization");
  } else if (input.getAll("code").length !== 1 || input.getAll("state").length !== 1) {
    redirect.searchParams.set("connection_error", "authorization");
  } else {
    const target = new URL(request.url);
    target.search = new URLSearchParams({ state: input.get("state") ?? "", code: input.get("code") ?? "" }).toString();
    const result = await proxyBackend(new Request(target, { headers: request.headers }), `api/connectors/${provider}/callback`);
    const failure = result.ok ? null : await failureCode(result);
    if (failure === "sign_in") return oauthSignInRedirect(request);
    redirect.searchParams.set(result.ok ? "connected" : "connection_error", result.ok ? provider : failure ?? "authorization");
  }
  return oauthRedirect(redirect);
}
