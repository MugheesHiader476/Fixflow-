import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const clerk = vi.hoisted(() => ({
  handler: null as null | ((auth: () => Promise<{ userId: string | null }>, request: NextRequest) => Promise<Response | undefined>),
}));

vi.mock("@clerk/nextjs/server", () => ({
  clerkMiddleware: (handler: typeof clerk.handler) => {
    clerk.handler = handler;
    return () => undefined;
  },
}));

import "@/proxy";

beforeEach(() => { vi.stubEnv("CONNECTORS__PUBLIC_URL", "https://app.example.test"); });
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

async function checkRoute(pathname: string, userId: string | null = null) {
  if (!clerk.handler) throw new Error("Clerk route guard was not registered");
  const auth = vi.fn().mockResolvedValue({ userId });
  const response = await clerk.handler(auth, new NextRequest(`http://127.0.0.1:3000${pathname}`));
  return { auth, response };
}

describe("account-first routing", () => {
  it.each(["/sign-up", "/sign-up/verify-email-address", "/sign-in", "/sign-in/factor-one", "/__clerk/something"])(
    "leaves %s available without an account",
    async (pathname) => {
      const { auth, response } = await checkRoute(pathname);
      expect(auth).not.toHaveBeenCalled();
      expect(response).toBeUndefined();
    },
  );

  it.each(["/", "/sources", "/history", "/saved", "/settings"])(
    "sends signed-out visitors of %s to sign-up",
    async (pathname) => {
      const { response } = await checkRoute(pathname);
      expect(response?.status).toBe(307);
      expect(new URL(response?.headers.get("Location") ?? "").pathname).toBe("/sign-up");
    },
  );

  it("allows signed-in visitors into the workspace", async () => {
    const { response } = await checkRoute("/sources", "user-1");
    expect(response).toBeUndefined();
  });

  it("rejects unsigned API requests without a page redirect", async () => {
    const { response } = await checkRoute("/api/backend/api/documents");
    expect(response?.status).toBe(401);
    expect(response?.headers.get("Location")).toBeNull();
  });

  it("opens only POST provider events to the signature boundary", async () => {
    if (!clerk.handler) throw new Error("Missing middleware");
    const auth = vi.fn().mockResolvedValue({ userId: null });
    const event = new NextRequest("http://localhost/api/connectors/events/github", { method: "POST" });
    expect(await clerk.handler(auth, event)).toBeUndefined();
    expect(auth).not.toHaveBeenCalled();
    expect((await checkRoute("/api/connectors/gmail/callback")).response?.status).toBe(303);
    expect((await checkRoute("/api/connectors/events/unknown")).response?.status).toBe(401);
  });

  it.each(["gmail", "google_drive", "github", "slack"])("sends an expired %s OAuth callback to sign-in without preserving credentials", async (provider) => {
    const { auth, response } = await checkRoute(`/api/connectors/${provider}/callback?code=private-code&state=private-state&redirect_url=https://attacker.test`);
    expect(auth).toHaveBeenCalledOnce();
    expect(response?.status).toBe(303);
    expect(response?.headers.get("Location")).toBe("https://app.example.test/sign-in?connection_error=sign_in");
    expect(response?.headers.get("Referrer-Policy")).toBe("no-referrer");
    expect(response?.headers.get("Cache-Control")).toBe("no-store");
  });

  it("keeps callback exchange protected and limits the redirect to exact GET callback routes", async () => {
    expect((await checkRoute("/api/connectors/slack/callback", "user-1")).response).toBeUndefined();
    expect((await checkRoute("/api/backend/api/connectors/slack/callback")).response?.status).toBe(401);
    expect((await checkRoute("/api/connectors/unknown/callback")).response?.status).toBe(401);
    if (!clerk.handler) throw new Error("Missing middleware");
    const response = await clerk.handler(async () => ({ userId: null }), new NextRequest("https://app.example.test/api/connectors/slack/callback", { method: "POST" }));
    expect(response?.status).toBe(401);
  });
});
