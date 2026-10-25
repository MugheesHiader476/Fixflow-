import { describe, expect, it } from "vitest";
import { diagnosisMarkdown } from "@/lib/diagnosis";
import { ASYNCIO_DIAGNOSIS } from "./fixtures/diagnosis";

describe("diagnosisMarkdown", () => {
  it("exports the diagnosis, steps, and source locations", () => {
    const markdown = diagnosisMarkdown(ASYNCIO_DIAGNOSIS);

    expect(markdown).toContain("# ProofOS diagnosis");
    expect(markdown).toContain("**Confidence:** 92%");
    expect(markdown).toContain("1. **Make the endpoint async**");
    expect(markdown).toContain("https://docs.python.org/3/library/asyncio-eventloop.html");
  });
});
