import { proxyFastApi } from "@/lib/server-api";

interface Context {
  params: Promise<{ projectId: string }>;
}

export async function GET(_request: Request, context: Context) {
  const { projectId } = await context.params;
  return proxyFastApi(`/api/v2/projects/${encodeURIComponent(projectId)}`);
}

export async function PATCH(request: Request, context: Context) {
  const { projectId } = await context.params;
  return proxyFastApi(
    `/api/v2/projects/${encodeURIComponent(projectId)}`,
    { method: "PATCH", body: await request.text() },
  );
}

export async function DELETE(_request: Request, context: Context) {
  const { projectId } = await context.params;
  return proxyFastApi(
    `/api/v2/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE" },
  );
}
