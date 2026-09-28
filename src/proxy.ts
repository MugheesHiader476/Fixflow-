import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

function isAccountRoute(pathname: string): boolean {
  return ["/sign-in", "/sign-up", "/__clerk"].some(
    (path) => pathname === path || pathname.startsWith(`${path}/`)
  );
}

export default clerkMiddleware(async (auth, request) => {
  const pathname = request.nextUrl.pathname;
  if (isAccountRoute(pathname)) return;

  const { userId } = await auth();
  if (userId) return;

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Sign in to use FixFlow." }, { status: 401 });
  }

  return NextResponse.redirect(new URL("/sign-up", request.url));
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};
