import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { proxyBackend } from "@/lib/server/backend-proxy";
import { proxyConnectorEvent } from "@/lib/server/connector-events";
import { GET } from "@/app/api/connectors/[provider]/callback/route";

vi.mock("@clerk/nextjs/server", () => ({ auth: async () => ({ userId: "user_verified" }) }));
const ID = "28d6ce92-c958-4010-b4ba-1784cf82dc10";

beforeEach(() => {
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

  it("returns OAuth callbacks to the configured public origin behind Docker", async () => {
    vi.stubEnv("CONNECTORS__PUBLIC_URL", "https://app.example.test/");
    // eslint-disable-next-line sonarjs/no-clear-text-protocols -- Reproduce Next's local Docker HTTP bind URL.
    const response = await GET(new Request("http://0.0.0.0:3000/api/connectors/gmail/callback?state=state&code=private-code", {
      headers: { Host: "attacker.example", "X-Forwarded-Host": "attacker.example" },
    }), { params: Promise.resolve({ provider: "gmail" }) });
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("https://app.example.test/connectors?connected=gmail");
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
