import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { proxyBackend } from "@/lib/server/backend-proxy";

vi.mock("@clerk/nextjs/server", () => ({ auth: async () => ({ userId: "user_verified" }) }));

describe("trusted backend gateway", () => {
  beforeEach(() => {
    vi.stubEnv("CONNECTORS__PUBLIC_URL", "");
    vi.stubEnv("INTERNAL_API_URL", "https://backend.example.test");
    vi.stubEnv("FIXFLOW_API_TOKEN", "test-only-server-token-with-32-characters");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json([])));
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it.each(["api/ask", "api/chat", "api/debug"])("allows bounded model loading for %s while retaining ordinary request deadlines", async (path) => {
    const deadline = vi.spyOn(AbortSignal, "timeout");
    await proxyBackend(new Request(`http://localhost/api/backend/${path}`, { method: "POST" }), path);
    expect(deadline).toHaveBeenLastCalledWith(120_000);
    await proxyBackend(new Request("http://localhost/api/backend/api/sources"), "api/sources");
    expect(deadline).toHaveBeenLastCalledWith(60_000);
  });

  it("replaces browser identity and credentials with the verified account", async () => {
    await proxyBackend(new Request("http://localhost/api/backend/api/sources", {
      headers: { Authorization: "Bearer forged", "X-FixFlow-User-Id": "user_victim" },
    }), "api/sources");
    expect(fetch).toHaveBeenCalledWith("https://backend.example.test/api/sources", expect.objectContaining({
      headers: expect.any(Headers), cache: "no-store", redirect: "error",
    }));
    const headers = vi.mocked(fetch).mock.calls[0][1]?.headers as Headers;
    expect(headers.get("X-FixFlow-User-Id")).toBe("user_verified");
    expect(headers.get("Authorization")).toBe("Bearer test-only-server-token-with-32-characters");
  });

  it.each([
    new Headers({ Origin: "https://attacker.example" }),
    new Headers({ "Sec-Fetch-Site": "cross-site" }),
  ])("rejects cross-site writes before forwarding", async (headers) => {
    const response = await proxyBackend(new Request("http://localhost/api/backend/api/debug", { method: "POST", headers }), "api/debug");
    expect(response.status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["http://localhost:3000", "https://app.example.test"])("accepts %s behind a Docker bind address", async (origin) => {
    vi.stubEnv("CONNECTORS__PUBLIC_URL", origin);
    // eslint-disable-next-line sonarjs/no-clear-text-protocols -- Reproduce Next's local Docker HTTP bind URL.
    const response = await proxyBackend(new Request("http://0.0.0.0:3000/api/backend/api/connectors/gmail/connect", {
      method: "POST", headers: { Origin: origin, "Sec-Fetch-Site": "same-origin" },
    }), "api/connectors/gmail/connect");
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledWith("https://backend.example.test/api/connectors/gmail/connect", expect.any(Object));
  });

  it.each([
    new Headers({ Origin: "https://attacker.example", Host: "attacker.example", "X-Forwarded-Host": "attacker.example" }),
    // eslint-disable-next-line sonarjs/no-clear-text-protocols -- The internal Docker bind address must never be accepted as a browser origin.
    new Headers({ Origin: "http://0.0.0.0:3000" }),
    new Headers({ Origin: "http://localhost:3000", "Sec-Fetch-Site": "cross-site" }),
  ])("keeps origin protection when a public URL is configured", async (headers) => {
    vi.stubEnv("CONNECTORS__PUBLIC_URL", "http://localhost:3000");
    // eslint-disable-next-line sonarjs/no-clear-text-protocols -- Reproduce Next's local Docker HTTP bind URL.
    const response = await proxyBackend(new Request("http://0.0.0.0:3000/api/backend/api/connectors/gmail/connect", {
      method: "POST", headers,
    }), "api/connectors/gmail/connect");
    expect(response.status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["invalid URL", "http://app.example.test", "https://user:password@app.example.test", "https://app.example.test/path", "https://app.example.test?query=1", "https://app.example.test#fragment"])(
    "fails closed for invalid public configuration: %s", async (origin) => {
      vi.stubEnv("CONNECTORS__PUBLIC_URL", origin);
      const response = await proxyBackend(new Request("http://localhost/api/backend/api/debug", { method: "POST" }), "api/debug");
      expect(response.status).toBe(503);
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it.each(["../health", "api/admin", "api/sources/../../health", "https://attacker.example"]) ("rejects non-allowlisted paths: %s", async (path) => {
    expect((await proxyBackend(new Request("http://localhost/api/backend/unknown"), path)).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects oversized uploads and fails closed without the server token", async () => {
    expect((await proxyBackend(new Request("http://localhost/api/documents", {
      method: "POST", headers: { "Content-Length": String(53 * 1024 * 1024) },
    }), "api/documents")).status).toBe(413);
    vi.stubEnv("FIXFLOW_API_TOKEN", "");
    expect((await proxyBackend(new Request("http://localhost/api/backend/api/sources"), "api/sources")).status).toBe(503);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("maps browser health to private account readiness", async () => {
    const response = await proxyBackend(new Request("http://localhost/api/backend/health"), "health");
    expect(fetch).toHaveBeenCalledWith("https://backend.example.test/api/readiness", expect.any(Object));
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it.each(["api/ask", "api/sources/11111111-1111-4111-8111-111111111111/availability"])("forwards authorized question/source writes through the gateway: %s", async (path) => {
    const response = await proxyBackend(new Request(`http://localhost/api/backend/${path}`, {
      method: "POST", body: JSON.stringify({ question: "Refund policy?", active: false }),
    }), path);
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledWith(`https://backend.example.test/${path}`, expect.objectContaining({ method: "POST" }));
  });
});
