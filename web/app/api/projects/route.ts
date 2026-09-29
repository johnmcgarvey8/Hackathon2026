import { proxyFastApi } from "@/lib/server-api";

export async function GET() {
  return proxyFastApi("/api/v2/projects");
}

export async function POST(request: Request) {
  return proxyFastApi("/api/v2/projects", {
    method: "POST",
    body: await request.text(),
  });
}
