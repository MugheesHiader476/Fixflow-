"use client";

/** External Clerk widgets only. Next navigation, gateway and application stay real. */
import type { ReactNode } from "react";

export function ClerkProvider({ children }: { children: ReactNode }) { return children; }
export function Show({ when, children }: { when: string; children: ReactNode }) {
  return when === "signed-in" ? children : null;
}
export function SignInButton({ children }: { children: ReactNode }) { return children; }
export function SignUpButton({ children }: { children: ReactNode }) { return children; }
export function UserButton() { return <span aria-label="Simulated Clerk identity">Test account</span>; }
export function SignIn() { return <p>External Clerk sign-in boundary simulated</p>; }
export function SignUp() { return <p>External Clerk sign-up boundary simulated</p>; }
export function useUser() { return { isLoaded: true, user: { fullName: "Test account", primaryEmailAddress: { emailAddress: "test@example.invalid" } } }; }
export function useClerk() { return { openUserProfile: () => {}, signOut: async () => {} }; }
