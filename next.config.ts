import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // OAuth callback queries contain single-use credentials. Keep ordinary request logs.
  logging: { incomingRequests: { ignore: [/\/api\/connectors\/[^/]+\/callback(?:\?|$)/] } },
  headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    }];
  },
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
