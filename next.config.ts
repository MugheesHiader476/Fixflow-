import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // Clerk proxy clones upload bodies; preserve the existing 50 MiB file limit plus multipart overhead.
    proxyClientMaxBodySize: 52 * 1024 * 1024,
    // The CLI type checker loses captured output under Node.js 24, causing
    // `next build` to fail before compilation. TypeScript 5 exposes the stable
    // compiler API, which avoids that subprocess entirely.
    useTypeScriptCli: false,
  },
};

export default nextConfig;
