// @vitest-environment node

import { describe, expect, it } from "vitest";
import { ESLint } from "eslint";
import ts from "typescript";
import { resolve } from "node:path";

describe("disposable browser runtime stays outside source validation", () => {
  it("ignores generated builds/uploads while retaining app and browser test lint rules", async () => {
    const eslint = new ESLint();
    for (const path of [".local/preembedding/app/.next/server/generated.js", ".local/preembedding/uploads/source.js"]) {
      expect(await eslint.calculateConfigForFile(path)).toBeUndefined();
    }
    for (const path of ["src/app/sources/page.tsx", "tests/browser/run-preembedding.mjs"]) {
      const config = await eslint.calculateConfigForFile(path);
      expect(config.rules["@typescript-eslint/no-unused-vars"]).toBeDefined();
      expect(config.rules["sonarjs/no-dead-store"]).toBeDefined();
    }
  }, 15000); // Loading the real Next/TypeScript ESLint plugins can exceed 5s in the full parallel suite.

  it("typechecks application/test sources without including the copied runtime application", () => {
    const root = process.cwd();
    const input = ts.readConfigFile(resolve(root, "tsconfig.json"), ts.sys.readFile);
    expect(input.error).toBeUndefined();
    const parsed = ts.parseJsonConfigFileContent(input.config, ts.sys, root);
    expect(parsed.errors).toEqual([]);
    expect(parsed.fileNames).toContain(resolve(root, "src/app/sources/page.tsx"));
    expect(parsed.fileNames).toContain(resolve(root, "tests/browser/clerk-client.tsx"));
    expect(parsed.fileNames.some((path) => path.startsWith(resolve(root, ".local") + "/"))).toBe(false);
  });
});
