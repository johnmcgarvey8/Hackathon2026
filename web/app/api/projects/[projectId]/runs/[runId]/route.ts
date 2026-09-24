import { proxyFastApi } from "@/lib/server-api";

interface Context {
  params: Promise<{ projectId: string; runId: string }>;
}

export async function GET(_request: Request, context: Context) {
  const { projectId, runId } = await context.params;
  return proxyFastApi(
    `/api/v2/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`,
  );
}

export async function DELETE(_request: Request, context: Context) {
  const { projectId, runId } = await context.params;
  return proxyFastApi(
    `/api/v2/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`,
    { method: "DELETE" },
  );
}
