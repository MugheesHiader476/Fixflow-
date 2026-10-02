import { proxyConnectorEvent } from "@/lib/server/connector-events";

export async function POST(request: Request, context: { params: Promise<{ provider: string }> }) {
  return proxyConnectorEvent(request, (await context.params).provider);
}
