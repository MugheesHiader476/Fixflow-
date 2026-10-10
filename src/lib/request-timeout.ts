// Local model loading shares the answer deadline; ordinary API requests remain bounded separately.
export function requestTimeoutMs(path: string, method = "GET"): number {
  return method.toUpperCase() === "POST" && /^\/?api\/(ask|debug|chat)$/.test(path) ? 120_000 : 60_000;
}
