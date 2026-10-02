import { proxyBackend } from "@/lib/server/backend-proxy";

type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, context: Context) {
  const { path } = await context.params;
  return proxyBackend(request, path.join("/"));
}

export { handle as GET, handle as POST };
