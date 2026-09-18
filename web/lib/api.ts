import type {
  ApiErrorPayload,
  ChatStatus,
  Conversation,
  CreateProjectRequest,
  MeasurementRun,
  Project,
  SendMessageRequest,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as ApiErrorPayload | null;
    throw new ApiError(payload?.error || "The service request failed.", response.status, payload?.detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  chatStatus: (projectId: string) =>
    request<ChatStatus>(`/api/projects/${encodeURIComponent(projectId)}/chat-status`),
  projects: () => request<Project[]>("/api/projects"),
  createProject: (body: CreateProjectRequest) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  project: (projectId: string) => request<Project>(`/api/projects/${encodeURIComponent(projectId)}`),
  runs: (projectId: string) =>
    request<MeasurementRun[]>(`/api/projects/${encodeURIComponent(projectId)}/runs`),
  conversations: (projectId: string) =>
    request<Conversation[]>(`/api/projects/${encodeURIComponent(projectId)}/conversations`),
  createConversation: (projectId: string) =>
    request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations`, {
      method: "POST",
    }),
  conversation: (projectId: string, conversationId: string) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}`,
    ),
  sendMessage: (projectId: string, conversationId: string, body: SendMessageRequest) =>
    request<Conversation>(
      `/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/messages`,
      { method: "POST", body: JSON.stringify(body) },
    ),
};
