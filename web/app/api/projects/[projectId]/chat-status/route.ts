import { proxyFastApi } from "@/lib/server-api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ projectId: string }> },
) {
  const { projectId } = await context.params;
  return proxyFastApi(`/api/v2/projects/${encodeURIComponent(projectId)}/chat-status`);
}
