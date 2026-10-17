import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { applicationOrigin, isCrossOriginWrite } from "./application-origin";
import { requestTimeoutMs } from "../request-timeout";

const UUID = "[0-9a-fA-F-]{36}";
const PROVIDER = "(gmail|github|google_drive|slack)";
const GET_PATH = new RegExp(`^(health|api/(readiness|sessions|sources|saved|connectors)|api/sessions/${UUID}(/messages)?|api/sources/${UUID}(/status)?|api/connectors/${UUID}/(resources|status)|api/evidence/sources/${UUID}/embedding-status|api/agent/(runs(/${UUID})?|files/${UUID}/download)|api/connectors/${PROVIDER}/callback)$`);
const POST_PATH = new RegExp(`^api/(agent/(runs|runs/${UUID}/cancel|actions/${UUID}/execute)|ask|debug|chat|saved|documents|sources/${UUID}/(retry-embedding|availability)|evidence/(search|answer|sources/${UUID}/(index|remove-index|embed))|connectors/${PROVIDER}/connect|connectors/${UUID}/(configure|sync|disconnect|health|query))$`);

function connectorQuery(request: Request, path: string): string | null {
  if (!path.startsWith("api/connectors/")) return "";
  const parameters = new URL(request.url).searchParams;
  let allowed: Record<string, number> = {};
  if (path.endsWith("/resources")) allowed = { cursor: 4096 };
  if (path.endsWith("/callback")) allowed = { state: 200, code: 4096 };
  for (const [key, value] of parameters) {
    const maximum = Object.entries(allowed).find(([name]) => name === key)?.[1];
    if (!maximum || !value || value.length > maximum || parameters.getAll(key).length !== 1 || value.includes("\0")) return null;
  }
  const query = parameters.toString();
  return query ? `?${query}` : "";
}

function responseHeaders(upstream: Response): Headers {
  const headers = new Headers({ "Content-Type": upstream.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  for (const name of ["Retry-After", "Content-Disposition"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

export async function proxyBackend(request: Request, path: string) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: "Sign in to use FixFlow." }, { status: 401 });
  const allowed = request.method === "GET" ? GET_PATH.test(path) : request.method === "POST" && POST_PATH.test(path);
  if (!allowed) return NextResponse.json({ error: "Unknown API route" }, { status: 404 });
  const query = connectorQuery(request, path);
  if (query === null) return NextResponse.json({ error: "Invalid connector query" }, { status: 422 });
  const expectedOrigin = applicationOrigin(request);
  if (!expectedOrigin) return NextResponse.json({ error: "Public application URL is not configured correctly" }, { status: 503 });
  if (isCrossOriginWrite(request, expectedOrigin)) {
    return NextResponse.json({ error: "Cross-origin writes are not allowed" }, { status: 403 });
  }
  const apiUrl = process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL;
  const token = process.env.FIXFLOW_API_TOKEN;
  if (!apiUrl || !token) return NextResponse.json({ error: "Backend connection is not configured" }, { status: 503 });
  const target = path === "health" ? "api/readiness" : path;
  try {
    const headers = new Headers({ Authorization: `Bearer ${token}`, "X-FixFlow-User-Id": userId });
    const contentType = request.headers.get("Content-Type");
    if (contentType) headers.set("Content-Type", contentType);
    const length = request.headers.get("Content-Length");
    if (length && Number(length) > 52 * 1024 * 1024) return NextResponse.json({ error: "Request exceeds upload limit" }, { status: 413 });
    const upstream = await fetch(`${apiUrl.replace(/\/$/, "")}/${target}${query}`, {
      method: request.method,
      headers,
      ...(request.method === "POST" ? { body: request.body, duplex: "half" } : {}),
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(requestTimeoutMs())]),
      redirect: "error",
      cache: "no-store",
    } as RequestInit & { duplex?: "half" });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders(upstream),
    });
  } catch {
    return NextResponse.json({ error: "Could not connect to the FixFlow backend." }, { status: 502 });
  }
}
