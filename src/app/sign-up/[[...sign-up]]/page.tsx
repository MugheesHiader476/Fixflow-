import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { SignUp } from "@clerk/nextjs";
import { AccountShell } from "@/components/layout/account-shell";

export default async function SignUpPage() {
  const { userId } = await auth();
  if (userId) redirect("/");

  return (
    <AccountShell>
      <SignUp />
    </AccountShell>
  );
}
