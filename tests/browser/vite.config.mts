import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const harness = fileURLToPath(new URL("./", import.meta.url));

// This harness is test-only. The Next.js application and Clerk proxy are unchanged.
export default defineConfig({
  root: harness,
  publicDir: `${root}public`,
  plugins: [react()],
  resolve: {
    alias: [
      { find: "@", replacement: `${root}src` },
      { find: "next/navigation", replacement: `${harness}navigation.ts` },
      { find: "next/link", replacement: `${harness}link.tsx` },
      { find: "next/image", replacement: `${harness}image.tsx` },
      { find: "@clerk/nextjs", replacement: `${harness}clerk.tsx` },
    ],
  },
  define: { "process.env.NEXT_PUBLIC_API_URL": JSON.stringify(process.env.FIXFLOW_TEST_API_URL || "http://127.0.0.1:8001") },
  server: {
    host: "127.0.0.1", port: 4173, strictPort: true, fs: { allow: [root] },
    proxy: {
      "/api/backend": {
        target: process.env.FIXFLOW_TEST_API_URL || "http://127.0.0.1:8001",
        rewrite: (path) => path.replace(/^\/api\/backend\/health$/, "/api/readiness").replace(/^\/api\/backend/, ""),
        // Test-only trusted principal. Production uses Clerk through the Next route.
        configure: (proxy) => proxy.on("proxyReq", (request) => {
          request.setHeader("Authorization", `Bearer ${process.env.FIXFLOW_API_TOKEN || ""}`);
          request.setHeader("X-FixFlow-User-Id", "user_browser_test");
        }),
      },
    },
  },
});
