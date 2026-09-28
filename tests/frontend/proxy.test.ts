import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

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

afterEach(() => {
  vi.clearAllMocks();
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
    const { response } = await checkRoute("/api/documents");
    expect(response?.status).toBe(401);
    expect(response?.headers.get("Location")).toBeNull();
  });
});
