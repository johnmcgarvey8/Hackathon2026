import { NextResponse } from "next/server";

const DEFAULT_BASE_URL = "http://127.0.0.1:8090";

export function upstreamUrl(path: string): string {
  const base = (process.env.FASTAPI_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function proxyFastApi(path: string, init?: RequestInit): Promise<NextResponse> {
  const token = process.env.FASTAPI_BEARER_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "Backend authentication is not configured. Add FASTAPI_BEARER_TOKEN to .env.local." },
      { status: 503 },
    );
  }

  try {
    const response = await fetch(upstreamUrl(path), {
      ...init,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
      cache: "no-store",
    });
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) {
      return NextResponse.json(
        {
          error:
            typeof body?.detail === "string"
              ? body.detail
              : `FastAPI returned HTTP ${response.status}.`,
          status: response.status,
          detail: body,
        },
        { status: response.status },
      );
    }
    return NextResponse.json(body, {
      status: response.status,
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch {
    return NextResponse.json(
      {
        error: "The FastAPI service is unavailable. Check FASTAPI_BASE_URL and start the backend.",
      },
      { status: 503 },
    );
  }
}

export async function proxyFastApiBinary(path: string): Promise<NextResponse> {
  const token = process.env.FASTAPI_BEARER_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "Backend authentication is not configured. Add FASTAPI_BEARER_TOKEN to .env.local." },
      { status: 503 },
    );
  }

  try {
    const response = await fetch(upstreamUrl(path), {
      headers: {
        Accept: "application/octet-stream, application/zip",
        Authorization: `Bearer ${token}`,
      },
      cache: "no-store",
    });
    if (!response.ok) {
      const contentType = response.headers.get("content-type") || "";
      const detail = contentType.includes("application/json")
        ? await response.json()
        : await response.text();
      return NextResponse.json(
        {
          error:
            typeof detail === "string"
              ? detail
              : typeof detail?.detail === "string"
                ? detail.detail
                : `FastAPI returned HTTP ${response.status}.`,
          status: response.status,
          detail,
        },
        { status: response.status },
      );
    }

    const headers = new Headers({
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    });
    for (const name of ["content-type", "content-disposition", "etag"]) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new NextResponse(response.body, { status: response.status, headers });
  } catch {
    return NextResponse.json(
      { error: "The FastAPI service is unavailable. Check FASTAPI_BASE_URL and start the backend." },
      { status: 503 },
    );
  }
}
