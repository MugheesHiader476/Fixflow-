# Workspace frontend refresh

Date: 2026-10-17. Scope: the user's photo/Claude layout references, with the parsing-only product boundary preserved.

## Implemented experience

- Warm charcoal default theme, an available persistent light-theme preference, serif workspace headings and restrained terracotta accents.
- A desktop sidebar with collapse/expand, active routes, real owner-scoped historical conversations, source/connectors/history/saved/settings destinations and a native modal mobile drawer.
- A centered source composer. Pasted content uses the existing authenticated document-ingestion API. Blank/duplicate submissions are blocked, pending requests cancel on unmount, failed submissions retain the typed source, and acceptance links to source status without claiming parsing finished.
- An add-source disclosure and a capability disclosure with Escape/outside dismissal. Chat stays disabled; the interface states that AI models are not enabled.
- Responsive local photographic hero with bounded typography and image cropping. It remains below the primary workspace, with an anchor back to the composer and a source-page link.
- Connected Apps uses searchable provider cards and an All apps / Connected filter. Server configuration, account states, selected-resource management, pagination, sync polling, reconnection and disconnect behavior remain authoritative.
- Account-entry copy and application metadata accurately describe document parsing.

No model inference, retrieval, embeddings, backend endpoints, database schema or connector permission behavior was added. Historical conversations remain readable/saveable. No provider was connected or messaged by this UI work.

## 3D and compatibility

The connector banner uses React Three Fiber 9.8.2 and Three.js 0.186.1. Fiber 9 pairs with React 19, as documented in the [official installation guidance](https://r3f.docs.pmnd.rs/getting-started/installation).

The scene is an original small geometric illustration, with no downloaded models, external textures, remote assets or inference. The decorative scene has no independent semantic content.

- React.lazy/Suspense isolates the Three.js code from the initial application bundle.
- IntersectionObserver loads/renders it only near the viewport.
- Small screens, reduced-motion preference, Save-Data and missing browser capability use a CSS fallback.
- WebGL initialization failure/import failure is contained by an error boundary.
- Context loss switches to the static fallback.
- Demand rendering, bounded device pixel ratio and low-power preference avoid a continuous animation loop.
- The static illustration preserves the banner's dimensions and content.

React Three Fiber's supported demand-rendering and fallback mechanisms are described in the [Canvas documentation](https://r3f.docs.pmnd.rs/api/canvas). The implementation also contains failures outside the canvas through the enclosing React boundary.

## Dependency security

Next.js and its lint configuration were patched from 16.3.5 to **16.3.8**. The Next version warning in AGENTS.md remains, with the version corrected. Relevant advisories include [the next/og issue](https://github.com/advisories/GHSA-vcvr-r3jv-pc5j) and [image optimization SSRF](https://github.com/advisories/GHSA-cjq9-62q9-8jv4). Audit detection of a vulnerable dependency is not evidence that this application exercised every affected feature.

Compatible lockfile updates also bring Sharp, source-map-js and brace-expansion to the available patched versions.

Final npm audit results:

- Production dependencies: **0 known findings**.
- Full dependency tree: **5 high-severity development-tool findings**, all in the eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces chain.
- The remaining underlying braces advisory affects the latest published 3.0.3; the automatic suggestion would downgrade the lint configuration across major framework versions. No force downgrade, global lint suppression or unreviewed custom dependency patch was applied.
- Review this development-tool finding before exposing build/lint tooling to untrusted patterns. Production dependency audit success is not a complete security assessment.

## Verification record

- ESLint: passed.
- TypeScript: passed.
- Frontend unit/integration suite with coverage: **132 passed**; line coverage **86.72%**.
- Optimized Next.js production build: passed on 16.3.8.
- Workspace browser harness: **6 checks passed**, including paste acceptance, disabled AI, disclosures, theme, desktop sidebar, source polling and mobile navigation.
- Connector browser harness: **7 checks passed**, including selection/pagination, health, sync polling, mobile modal focus/Escape, disconnect and redirect boundaries.
- Visual/3D browser harness: **6 checks passed**, including actual WebGL rendering, filters, context loss, reduced motion, WebGL-disabled fallback and loaded hero/no horizontal overflow at 320/390/768/1024/1920px.
- Isolated standalone production smoke checks on localhost:3011: optimized hero image HTTP 200 with image content type, account-entry HTTP 200, signed-out gateway HTTP 401, protected home redirect to sign-up. The temporary server was stopped afterwards.
- Python/backend tests and migrations: not rerun; no backend/schema changes.
- Live signed-in Clerk sessions, provider consent/event delivery, deployment and real data ingestion: not verified by these mocked-boundary browser checks.

Browser output is ignored under .local/browser-evidence/. The harness exercises real React, CSS and Three.js but mocks Clerk/Next adapters and gateway/provider HTTP. SwiftShader validates the render path, not performance on every physical GPU. Visual review included desktop dark/light, mobile workspace, responsive hero and connector pages.

Reproduce browser checks with the installed external Playwright package path in FIXFLOW_BROWSER_TOOLS after starting the existing Vite harness. No test-only identity or mock routing was added to production code.
