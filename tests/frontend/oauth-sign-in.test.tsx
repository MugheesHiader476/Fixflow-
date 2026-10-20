import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SignInPage from "@/app/sign-in/[[...sign-in]]/page";
import { CONNECTION_ERRORS, OAUTH_SIGN_IN_RETURN } from "@/lib/oauth-errors";

const session = vi.hoisted(() => ({ userId: null as string | null }));
const navigation = vi.hoisted(() => ({ redirect: vi.fn((url: string): never => { throw new Error(`redirect:${url}`); }) }));
vi.mock("@clerk/nextjs/server", () => ({ auth: async () => session }));
vi.mock("next/navigation", () => navigation);
vi.mock("@/components/layout/account-shell", () => ({ AccountShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock("@clerk/nextjs", () => ({ SignIn: ({ forceRedirectUrl }: { forceRedirectUrl?: string }) => <div data-testid="clerk-sign-in" data-return={forceRedirectUrl} /> }));

beforeEach(() => { session.userId = null; vi.clearAllMocks(); });
afterEach(cleanup);

describe("OAuth session-expiry recovery", () => {
  it("keeps the prebuilt sign-in and returns to a fresh connection notice", async () => {
    render(await SignInPage({ searchParams: Promise.resolve({ connection_error: "sign_in" }) }));
    expect(screen.getByRole("status").textContent).toBe(CONNECTION_ERRORS.sign_in);
    expect(screen.getByTestId("clerk-sign-in").getAttribute("data-return")).toBe(OAUTH_SIGN_IN_RETURN);
  });

  it.each([undefined, "private-provider-error", ["sign_in", "private-code"]])("preserves normal sign-in for other query values: %s", async (value) => {
    render(await SignInPage({ searchParams: Promise.resolve({ connection_error: value, redirect_url: "https://attacker.test" }) }));
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByTestId("clerk-sign-in").getAttribute("data-return")).toBeNull();
  });

  it("returns an already restored session directly to Connected Apps", async () => {
    session.userId = "user-1";
    await expect(SignInPage({ searchParams: Promise.resolve({ connection_error: "sign_in" }) })).rejects.toThrow(`redirect:${OAUTH_SIGN_IN_RETURN}`);
    expect(navigation.redirect).toHaveBeenCalledWith(OAUTH_SIGN_IN_RETURN);
  });
});
