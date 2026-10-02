import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { proxyBackend } from "@/lib/server/backend-proxy";

vi.mock("@clerk/nextjs/server", () => ({ auth: async () => ({ userId: "user_verified" }) }));

describe("trusted backend gateway", () => {
  beforeEach(() => {
    vi.stubEnv("INTERNAL_API_URL", "https://backend.example.test");
    vi.stubEnv("FIXFLOW_API_TOKEN", "test-only-server-token-with-32-characters");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json([])));
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

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
});
