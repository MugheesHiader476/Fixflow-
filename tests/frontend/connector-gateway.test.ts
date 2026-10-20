import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { proxyBackend } from "@/lib/server/backend-proxy";
import { proxyConnectorEvent } from "@/lib/server/connector-events";
import { GET } from "@/app/api/connectors/[provider]/callback/route";

const session = vi.hoisted(() => ({ userId: "user_verified" as string | null }));
vi.mock("@clerk/nextjs/server", () => ({ auth: async () => session }));
const ID = "28d6ce92-c958-4010-b4ba-1784cf82dc10";

beforeEach(() => {
  session.userId = "user_verified";
  vi.stubEnv("CONNECTORS__PUBLIC_URL", "");
  vi.stubEnv("INTERNAL_API_URL", "https://backend.example.test");
  vi.stubEnv("FIXFLOW_API_TOKEN", "test-server-token-with-at-least-32-characters");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ accepted: true })));
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("connector gateway", () => {
  it("forwards one bounded resource cursor and rejects unapproved parameters", async () => {
    const path = `api/connectors/${ID}/resources`;
    expect((await proxyBackend(new Request(`http://localhost/${path}?cursor=page%20two`), path)).status).toBe(200);
    expect(fetch).toHaveBeenCalledWith(`https://backend.example.test/${path}?cursor=page+two`, expect.any(Object));
    vi.mocked(fetch).mockClear();
    expect((await proxyBackend(new Request(`http://localhost/${path}?cursor=one&cursor=two`), path)).status).toBe(422);
    expect((await proxyBackend(new Request(`http://localhost/${path}?url=https://attacker.test`), path)).status).toBe(422);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("completes server callback, removes OAuth extras, and redirects without code leakage", async () => {
    const response = await GET(new Request("http://localhost/api/connectors/gmail/callback?state=state&code=private-code&scope=read&authuser=0"), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("http://localhost/connectors?connected=gmail");
    expect(fetch).toHaveBeenCalledWith("https://backend.example.test/api/connectors/gmail/callback?state=state&code=private-code", expect.any(Object));
    expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
  });

  it("maps denied authorization to a fixed message without forwarding", async () => {
    const response = await GET(new Request("http://localhost/api/connectors/gmail/callback?error=private-provider-description"), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.headers.get("Location")).toBe("http://localhost/connectors?connection_error=authorization");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("returns a fixed actionable error code without reflecting provider messages or codes", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ error: { code: "MISSING_SCOPE", message: "private-provider-response" } }, { status: 422 }));
    const response = await GET(new Request("http://localhost/api/connectors/google_drive/callback?state=state&code=private-code"), { params: Promise.resolve({ provider: "google_drive" }) });
    expect(response.headers.get("Location")).toBe("http://localhost/connectors?connection_error=missing_scope");
  });

  it("handles a session expiring after the route guard without forwarding or retaining the code", async () => {
    session.userId = null;
    const response = await GET(new Request("http://localhost/api/connectors/gmail/callback?state=private-state&code=private-code"), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.headers.get("Location")).toBe("http://localhost/sign-in?connection_error=sign_in");
    expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("distinguishes a rejected backend gateway from an expired browser session", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ error: { code: "HTTP_ERROR", message: "private-backend-error" } }, { status: 401 }));
    const response = await GET(new Request("http://localhost/api/connectors/gmail/callback?state=state&code=private-code"), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.headers.get("Location")).toBe("http://localhost/connectors?connection_error=not_configured");
  });

  it.each([
    ["access_denied", "oauth_access_denied"], ["bad_redirect_uri", "oauth_redirect_mismatch"],
    ["invalid_client", "oauth_client_invalid"], ["invalid_scope", "oauth_scope_invalid"],
    ["admin_policy_enforced", "oauth_access_denied"], ["server_error", "provider_unavailable"],
    ["private-unknown-error", "authorization"],
  ])("maps provider callback failure %s to a fixed category", async (error, category) => {
    const response = await GET(new Request(`http://localhost/api/connectors/slack/callback?error=${error}&error_description=private-provider-value&code=private-code`), { params: Promise.resolve({ provider: "slack" }) });
    expect(response.headers.get("Location")).toBe(`http://localhost/connectors?connection_error=${category}`);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["OAUTH_CLIENT_INVALID", "OAUTH_CODE_INVALID", "OAUTH_REDIRECT_MISMATCH", "OAUTH_EXCHANGE_UNCERTAIN", "OAUTH_REFRESH_UNCERTAIN"])("preserves safe backend category %s without its private description", async (code) => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ error: { code, message: "private-provider-value" } }, { status: 422 }));
    const response = await GET(new Request("http://localhost/api/connectors/gmail/callback?state=state&code=private-code"), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.headers.get("Location")).toBe(`http://localhost/connectors?connection_error=${code.toLowerCase()}`);
  });

  it("returns OAuth callbacks to the configured public origin behind Docker", async () => {
    vi.stubEnv("CONNECTORS__PUBLIC_URL", "https://app.example.test/");
    // eslint-disable-next-line sonarjs/no-clear-text-protocols -- Reproduce Next's local Docker HTTP bind URL.
    const response = await GET(new Request("http://0.0.0.0:3000/api/connectors/gmail/callback?state=state&code=private-code", {
      headers: { Host: "attacker.example", "X-Forwarded-Host": "attacker.example" },
    }), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("https://app.example.test/connectors?connected=gmail");
  });

  it("keeps the local Slack HTTPS callback on the signed-in origin", async () => {
    vi.stubEnv("CONNECTORS__PUBLIC_URL", "https://localhost:3000");
    const response = await GET(new Request("https://localhost:3000/api/connectors/slack/callback?state=state&code=private-code", {
      headers: { Cookie: "owner-session" },
    }), { params: Promise.resolve({ provider: "slack" }) });
    expect(response.headers.get("Location")).toBe("https://localhost:3000/connectors?connected=slack");
    expect(fetch).toHaveBeenCalledWith("https://backend.example.test/api/connectors/slack/callback?state=state&code=private-code", expect.any(Object));
    const headers = vi.mocked(fetch).mock.calls[0][1]?.headers as Headers;
    expect(headers.get("X-FixFlow-User-Id")).toBe("user_verified");
    expect(headers.get("Cookie")).toBeNull();
  });

  it("does not redirect callbacks when the public URL is invalid", async () => {
    vi.stubEnv("CONNECTORS__PUBLIC_URL", "https://app.example.test/private");
    const response = await GET(new Request("http://localhost/api/connectors/gmail/callback?state=state&code=private-code"), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.status).toBe(503);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("forwards event bytes/signatures with no trusted user or service token", async () => {
    const response = await proxyConnectorEvent(new Request("http://localhost/api/connectors/events/github", { method: "POST", body: "signed-bytes", headers: { "X-Hub-Signature-256": "signature", "X-FixFlow-User-Id": "forged", Cookie: "private-session" } }), "github");
    expect(response.status).toBe(200);
    const init = vi.mocked(fetch).mock.calls[0][1];
    const headers = init?.headers as Headers;
    expect(headers.get("X-Hub-Signature-256")).toBe("signature");
    expect(headers.get("X-FixFlow-User-Id")).toBeNull();
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("Cookie")).toBeNull();
  });

  it("rejects oversized or unknown event destinations before forwarding", async () => {
    expect((await proxyConnectorEvent(new Request("http://localhost/event", { method: "POST", body: "x".repeat(1024 * 1024 + 1) }), "github")).status).toBe(413);
    expect((await proxyConnectorEvent(new Request("http://localhost/event"), "../admin")).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });
});
