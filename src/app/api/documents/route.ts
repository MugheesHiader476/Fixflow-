import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: "Sign in to upload documentation." }, { status: 401 });
  }
  const apiUrl = process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) {
    return NextResponse.json({ error: "Backend URL is not configured" }, { status: 503 });
  }
  try {
    // Stream the existing route to the single backend ingestion implementation.
    const upstream = await fetch(`${apiUrl.replace(/\/$/, "")}/api/documents`, {
      method: "POST",
      body: request.body,
      headers: { "Content-Type": request.headers.get("Content-Type") || "application/octet-stream" },
      signal: request.signal,
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Could not connect to the FixFlow backend." }, { status: 502 });
  }
}
