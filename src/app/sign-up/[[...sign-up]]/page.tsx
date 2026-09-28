import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { SignUp } from "@clerk/nextjs";

export default async function SignUpPage() {
  const { userId } = await auth();
  if (userId) redirect("/");

  return (
    <div className="flex min-h-screen items-center justify-center">
      <SignUp />
    </div>
  );
}
