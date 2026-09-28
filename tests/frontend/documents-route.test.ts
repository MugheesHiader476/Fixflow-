import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "@/app/api/documents/route";

const clerk = vi.hoisted(() => ({ userId: "test-user" as string | null }));
vi.mock("@clerk/nextjs/server", () => ({
  auth: async () => ({ userId: clerk.userId }),
}));

afterEach(() => {
  clerk.userId = "test-user";
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("document upload proxy", () => {
  it("rejects uploads without a signed-in account", async () => {
    clerk.userId = null;
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await POST(new Request("http://localhost/api/documents", { method: "POST" }));
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("streams uploads to FastAPI and preserves 202 status", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000");
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ source_id: "source", status: "uploaded" }, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    form.set("file", new File(["# Guide"], "guide.md"));
    const request = new Request("http://localhost/api/documents", { method: "POST", body: form });
    const response = await POST(request);
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({ source_id: "source", status: "uploaded" });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/documents", expect.objectContaining({
      body: request.body, duplex: "half",
    }));
  });

  it("uses the internal backend URL from the Next.js server", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("INTERNAL_API_URL", "http://127.0.0.1:8001");
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ status: "uploaded" }, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const response = await POST(new Request("http://localhost/api/documents", { method: "POST" }));
    expect(response.status).toBe(202);
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8001/api/documents", expect.any(Object));
  });

  it("preserves backend validation errors", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ error: "Unsupported document type" }, { status: 415 })));
    const response = await POST(new Request("http://localhost/api/documents", { method: "POST" }));
    expect(response.status).toBe(415);
  });

  it("never falls back to JSONL or fake sources", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await POST(new Request("http://localhost/api/documents", { method: "POST" }));
    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns a safe error if the backend cannot be reached", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private failure")));
    const response = await POST(new Request("http://localhost/api/documents", { method: "POST" }));
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("private");
  });
});
