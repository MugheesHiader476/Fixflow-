/** Test-only external identity simulator, aliased only in an isolated Next checkout. */
import { cookies } from "next/headers";

const identities = new Set(["user_preembedding_a", "user_preembedding_b"]);
export async function auth() {
  const value = (await cookies()).get("fixflow-test-identity")?.value;
  return { userId: identities.has(value) ? value : null };
}
export function clerkMiddleware(handler) {
  return (request) => handler(auth, request);
}
