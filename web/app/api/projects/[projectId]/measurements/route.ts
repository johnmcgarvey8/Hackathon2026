import { proxyFastApi } from "@/lib/server-api";

interface Context {
  params: Promise<{ projectId: string }>;
}

export async function POST(request: Request, context: Context) {
  const { projectId } = await context.params;
  return proxyFastApi(
    `/api/v2/projects/${encodeURIComponent(projectId)}/briefs`,
    { method: "POST", body: await request.text() },
  );
}
