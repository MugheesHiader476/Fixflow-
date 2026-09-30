import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { SignIn } from "@clerk/nextjs";
import { AccountShell } from "@/components/layout/account-shell";

export default async function SignInPage() {
  const { userId } = await auth();
  if (userId) redirect("/");

  return (
    <AccountShell>
      <SignIn />
    </AccountShell>
  );
}
