/** Use the configured public origin rather than a Docker/reverse-proxy bind address. */
export function applicationOrigin(request: Request): string | null {
  const configured = process.env.CONNECTORS__PUBLIC_URL;
  if (!configured) return new URL(request.url).origin;
  try {
    const url = new URL(configured);
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    if ((url.protocol !== "https:" && !(url.protocol === "http:" && local))
      || url.username || url.password || !["", "/"].includes(url.pathname) || url.search || url.hash) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function isCrossOriginWrite(request: Request, expectedOrigin: string): boolean {
  if (request.method !== "POST") return false;
  const origin = request.headers.get("Origin");
  return Boolean((origin && origin !== expectedOrigin) || request.headers.get("Sec-Fetch-Site") === "cross-site");
}
