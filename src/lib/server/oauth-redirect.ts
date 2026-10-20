import { NextResponse } from "next/server";
import { applicationOrigin } from "./application-origin";

const HEADERS = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };

export function oauthRedirect(target: URL): NextResponse {
  return NextResponse.redirect(target, { status: 303, headers: HEADERS });
}

export function oauthSignInRedirect(request: Request): NextResponse {
  const origin = applicationOrigin(request);
  if (!origin) return NextResponse.json({ error: "Public application URL is not configured correctly" }, { status: 503, headers: HEADERS });
  const target = new URL("/sign-in", origin);
  target.searchParams.set("connection_error", "sign_in");
  return oauthRedirect(target);
}
