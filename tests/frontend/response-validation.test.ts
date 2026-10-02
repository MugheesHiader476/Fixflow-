import { describe, expect, it } from "vitest";
import { validResponse } from "@/lib/response-validation";
import { ASYNCIO_DIAGNOSIS } from "./fixtures/diagnosis";

describe("frontend response boundary", () => {
  it("accepts a complete persisted diagnosis", () => {
    expect(validResponse("/api/debug", ASYNCIO_DIAGNOSIS)).toBe(true);
  });
  it.each([
    { ...ASYNCIO_DIAGNOSIS, request: { techs: [], error: {} } },
    { ...ASYNCIO_DIAGNOSIS, request: { techs: [], files: [{ name: "source.ts", content: null }] } },
    { ...ASYNCIO_DIAGNOSIS, confidence: 101 },
    { ...ASYNCIO_DIAGNOSIS, rag: { ...ASYNCIO_DIAGNOSIS.rag, retrieved: -1 } },
  ])("rejects malformed rendering and restoration fields", (value) => {
    expect(validResponse("/api/debug", value)).toBe(false);
  });
  it("rejects misleading source and incomplete health responses", () => {
    expect(validResponse("/api/sources", [{ status: "indexed", chunks: -1 }])).toBe(false);
    expect(validResponse("/health", { status: "ok", service: "fixflow-api" })).toBe(false);
  });
});
