import { proxyFastApi } from "@/lib/server-api";

interface Context {
  params: Promise<{ projectId: string }>;
}

export async function GET(_request: Request, context: Context) {
  const { projectId } = await context.params;
  return proxyFastApi(`/api/v2/projects/${encodeURIComponent(projectId)}/conversations`);
}

export async function POST(request: Request, context: Context) {
  const { projectId } = await context.params;
  const url = new URL(request.url);
  const runId = url.searchParams.get("run_id");
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return proxyFastApi(
    `/api/v2/projects/${encodeURIComponent(projectId)}/conversations${query}`,
    { method: "POST" },
  );
}
