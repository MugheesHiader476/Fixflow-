import { ASYNCIO_DIAGNOSIS } from "./fixtures/diagnosis";
import { afterEach, describe, expect, it, vi } from "vitest";

async function loadApi(apiUrl = "/api/backend") {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_API_URL", apiUrl);
  return import("@/lib/api");
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const HEALTH = {
  status: "ok", service: "fixflow-api", api: "ok", database: "connected", pgvector: "available", schema: "ready",
  revision: "0002", expected_revision: "0002", sources: 0, documents: 0, chunks: 0, embedded_chunks: 0,
  pending_sources: 0, failed_sources: 0, embedding_configured: false, ai_generation: "not_configured",
};

const SOURCE = {
  id: "source", source_id: "source", name: "Guide", kind: "docs", source_type: "docs", status: "ready_for_embedding",
  documents: 1, document_count: 1, chunks: 1, chunk_count: 1, error_message: null,
  created_at: "2026-10-02T00:00:00Z", updated: "2026-10-02T00:00:00Z", detail: "1 document",
};

describe("backend API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("gives answers time for local model loading and preserves ordinary request deadlines", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(ASYNCIO_DIAGNOSIS)));
    const deadline = vi.spyOn(AbortSignal, "timeout");
    const { diagnose, checkBackendHealth } = await loadApi();
    await diagnose({ error: "failure", techs: [] });
    expect(deadline).toHaveBeenLastCalledWith(120_000);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(HEALTH)));
    await checkBackendHealth();
    expect(deadline).toHaveBeenLastCalledWith(60_000);
  });

  it("does not force a JSON content type onto GET requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(HEALTH)
    );
    vi.stubGlobal("fetch", fetchMock);
    const { checkBackendHealth } = await loadApi();

    await expect(checkBackendHealth()).resolves.toEqual(HEALTH);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("encodes session IDs before adding them to a URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ...ASYNCIO_DIAGNOSIS, sessionId: "encoded" }));
    vi.stubGlobal("fetch", fetchMock);
    const { getSession } = await loadApi();

    await getSession("folder/name?admin=true");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/backend/api/sessions/folder%2Fname%3Fadmin%3Dtrue",
      expect.any(Object)
    );
  });

  it("surfaces structured API errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ success: false, error: { message: "Session not found" } }, 404)
      )
    );
    const { getSession } = await loadApi();

    await expect(getSession("missing")).rejects.toThrow("Session not found");
  });

  it("serializes diagnosis, chat, and saved-solution requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(ASYNCIO_DIAGNOSIS))
      .mockResolvedValueOnce(jsonResponse({ id: "reply", role: "fixflow", text: "Documentation", sources: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: "saved", problem: "failure", rootCause: "cause", technology: [], fixSummary: "fix", sources: [], savedAt: "2026-10-02T00:00:00Z" }));
    vi.stubGlobal("fetch", fetchMock);
    const { diagnose, saveSolution, sendFollowUp } = await loadApi();

    await diagnose({ error: "failure", repoUrl: "https://example.test/repo", techs: ["React"] });
    await sendFollowUp("Why?", "session/1");
    await saveSolution({
      problem: "failure",
      rootCause: "cause",
      technology: ["React"],
      fixSummary: "fix",
      sources: [{ title: "Docs", type: "docs" }],
    });

    const requests = fetchMock.mock.calls.map(([, init]) => init as RequestInit);
    expect(requests).toHaveLength(3);
    expect(JSON.parse(String(requests[0].body))).toMatchObject({
      error: "failure",
      repo_url: "https://example.test/repo",
    });
    expect(JSON.parse(String(requests[1].body))).toEqual({
      question: "Why?",
      session_id: "session/1",
    });
    expect(requests.every((request) => new Headers(request.headers).has("Content-Type"))).toBe(true);
  });

  it("loads collection endpoints", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse([])));
    vi.stubGlobal("fetch", fetchMock);
    const { listKnowledgeSources, listSaved, listSessions } = await loadApi();

    await Promise.all([listKnowledgeSources(), listSaved(), listSessions()]);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/backend/api/sources",
      "/api/backend/api/saved",
      "/api/backend/api/sessions",
    ]);
  });

  it("encodes source IDs when checking ingestion status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(SOURCE));
    vi.stubGlobal("fetch", fetchMock);
    const { getSourceStatus } = await loadApi();
    await getSourceStatus("source/path");
    expect(fetchMock).toHaveBeenCalledWith("/api/backend/api/sources/source%2Fpath/status", expect.any(Object));
  });

  it("passes document uploads through multipart form data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(SOURCE));
    vi.stubGlobal("fetch", fetchMock);
    const { addKnowledgeSource } = await loadApi();

    await addKnowledgeSource({
      kind: "docs",
      value: "Runbook",
      content: "# Runbook",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("content")).toBe("# Runbook");
  });

  it("uses the upload endpoint's safe error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ error: "Unsupported document type." }, 415))
    );
    const { addKnowledgeSource } = await loadApi();

    await expect(
      addKnowledgeSource({ kind: "upload", value: "payload.exe" })
    ).rejects.toThrow("Unsupported document type.");
  });

  it("wraps network failures and preserves their cause", async () => {
    const failure = new TypeError("network unavailable");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(failure));
    const { listSaved } = await loadApi();

    const request = listSaved();
    await expect(request).rejects.toThrow("Could not connect to the FixFlow backend.");
    await expect(request).rejects.toHaveProperty("cause", failure);
  });

  it("always uses the authenticated same-origin gateway", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    const { listSessions } = await loadApi("https://untrusted.example");
    await listSessions();
    expect(fetchMock).toHaveBeenCalledWith("/api/backend/api/sessions", expect.any(Object));
  });

  it("reports degraded readiness without hiding database details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ ...HEALTH, status: "degraded", schema: "migration_required" }, 503)));
    const { checkBackendHealth } = await loadApi();
    await expect(checkBackendHealth()).resolves.toMatchObject({ status: "degraded", service: "fixflow-api", schema: "migration_required" });
  });

  it("rejects malformed successful responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not-json")));
    const { listSaved } = await loadApi();
    await expect(listSaved()).rejects.toThrow("invalid response");
  });

  it("loads persisted messages with an encoded session ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    const { listMessages } = await loadApi();
    await listMessages("folder/name");
    expect(fetchMock).toHaveBeenCalledWith("/api/backend/api/sessions/folder%2Fname/messages", expect.any(Object));
  });
});
