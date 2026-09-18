import { proxyFastApi } from "@/lib/server-api";

interface Context {
  params: Promise<{ projectId: string; conversationId: string }>;
}

export async function POST(request: Request, context: Context) {
  const { projectId, conversationId } = await context.params;
  const body = await request.text();
  return proxyFastApi(
    `/api/v2/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/messages`,
    { method: "POST", body },
  );
}
