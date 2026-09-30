import type { ReactNode } from "react";
import { UserRound } from "lucide-react";
export function Show({ when, children }: { when: string; children: ReactNode }) { return when === "signed-in" ? <>{children}</> : null; }
export function SignInButton({ children }: { children: ReactNode }) { return <>{children}</>; }
export function SignUpButton({ children }: { children: ReactNode }) { return <>{children}</>; }
export function UserButton() { return <span aria-label="Test account" className="rounded-full border border-border p-2"><UserRound size={15} /></span>; }
