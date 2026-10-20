import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { SignIn } from "@clerk/nextjs";
import { AccountShell } from "@/components/layout/account-shell";
import { CONNECTION_ERRORS, OAUTH_SIGN_IN_RETURN } from "@/lib/oauth-errors";

export default async function SignInPage({ searchParams }: {
  searchParams: Promise<{ connection_error?: string | string[] }>;
}) {
  const expiredOAuth = (await searchParams).connection_error === "sign_in";
  const { userId } = await auth();
  if (userId) redirect(expiredOAuth ? OAUTH_SIGN_IN_RETURN : "/");

  return (
    <AccountShell>
      {expiredOAuth && <p role="status" className="mb-4 text-sm text-muted">{CONNECTION_ERRORS.sign_in}</p>}
      <SignIn {...(expiredOAuth ? { forceRedirectUrl: OAUTH_SIGN_IN_RETURN } : {})} />
    </AccountShell>
  );
}
