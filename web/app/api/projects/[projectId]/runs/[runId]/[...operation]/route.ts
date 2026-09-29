import { proxyFastApi, proxyFastApiBinary } from "@/lib/server-api";

interface Context {
  params: Promise<{ projectId: string; runId: string; operation: string[] }>;
}

async function upstreamPath(request: Request, context: Context) {
  const { projectId, runId, operation } = await context.params;
  const suffix = operation.map(encodeURIComponent).join("/");
  const supplied = new URL(request.url).searchParams;
  const safeQuery = new URLSearchParams();
  for (const name of ["definition_version", "measurement_hash"]) {
    const value = supplied.get(name);
    if (value) safeQuery.set(name, value);
  }
  const query = safeQuery.size ? `?${safeQuery}` : "";
  return {
    operation,
    path: `/api/v2/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/${suffix}${query}`,
  };
}

export async function GET(request: Request, context: Context) {
  const { operation, path } = await upstreamPath(request, context);
  const binary = operation.at(-1) === "download"
    || (operation[0] === "artifacts" && operation.length === 2);
  return binary ? proxyFastApiBinary(path) : proxyFastApi(path);
}

export async function POST(request: Request, context: Context) {
  const { path } = await upstreamPath(request, context);
  return proxyFastApi(path, { method: "POST", body: await request.text() });
}

export async function PUT(request: Request, context: Context) {
  const { path } = await upstreamPath(request, context);
  return proxyFastApi(path, { method: "PUT", body: await request.text() });
}
