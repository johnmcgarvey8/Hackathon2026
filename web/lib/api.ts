import type {
  ApiErrorPayload,
  ArtifactMetadata,
  BinaryArtifact,
  BrandEvidenceAssessment,
  ChatStatus,
  ContentStrategyResponse,
  Conversation,
  CreateProjectRequest,
  EvidenceSource,
  ExportResponse,
  JobMutationResponse,
  MeasurementBriefRequest,
  MeasurementRun,
  Project,
  QueryPair,
  RunEvents,
  RunProgress,
  SendMessageRequest,
  WorkflowJob,
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

async function errorFrom(response: Response) {
  const payload = (await response.json().catch(() => null)) as ApiErrorPayload | null;
  return new ApiError(payload?.error || "The service request failed.", response.status, payload?.detail);
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
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}

async function requestBinary(path: string): Promise<BinaryArtifact> {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw await errorFrom(response);
  const disposition = response.headers.get("content-disposition");
  const filename = disposition?.match(/filename="?([^";]+)"?/i)?.[1] || null;
  return {
    blob: await response.blob(),
    mediaType: response.headers.get("content-type") || "application/octet-stream",
    filename,
    etag: response.headers.get("etag"),
  };
}

const projectPath = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}`;
const runPath = (projectId: string, runId: string) =>
  `${projectPath(projectId)}/runs/${encodeURIComponent(runId)}`;

export const api = {
  chatStatus: (projectId: string) => request<ChatStatus>(`${projectPath(projectId)}/chat-status`),
  projects: () => request<Project[]>("/api/projects"),
  createProject: (body: CreateProjectRequest) =>
    request<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  project: (projectId: string) => request<Project>(projectPath(projectId)),
  runs: (projectId: string) => request<MeasurementRun[]>(`${projectPath(projectId)}/runs`),
  createMeasurement: (projectId: string, body: MeasurementBriefRequest) =>
    request<MeasurementRun>(`${projectPath(projectId)}/measurements`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  run: (projectId: string, runId: string) => request<MeasurementRun>(runPath(projectId, runId)),
  prepareRun: (projectId: string, runId: string, expectedRevision: number) =>
    request<JobMutationResponse>(`${runPath(projectId, runId)}/prepare`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        idempotency_key: crypto.randomUUID(),
        confirm_preparation_calls: true,
      }),
    }),
  reviseQueries: (projectId: string, runId: string, expectedRevision: number, queries: QueryPair[]) =>
    request<MeasurementRun>(`${runPath(projectId, runId)}/queries`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: expectedRevision, queries }),
    }),
  approveQueries: (projectId: string, runId: string, expectedRevision: number, inputHash: string) =>
    request<MeasurementRun>(`${runPath(projectId, runId)}/query-approval`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, input_hash: inputHash }),
    }),
  startRun: (
    projectId: string,
    runId: string,
    expectedRevision: number,
    includeRecommendations: boolean,
  ) =>
    request<JobMutationResponse>(`${runPath(projectId, runId)}/start`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        idempotency_key: crypto.randomUUID(),
        confirm_evaluation_calls: true,
        include_recommendations: includeRecommendations,
      }),
    }),
  recoverEvaluators: (projectId: string, runId: string, expectedRevision: number) =>
    request<JobMutationResponse>(`${runPath(projectId, runId)}/recover-evaluators`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        idempotency_key: crypto.randomUUID(),
        confirm_evaluation_calls: true,
      }),
    }),
  runEvents: (projectId: string, runId: string) =>
    request<RunEvents>(`${runPath(projectId, runId)}/events`),
  runJobs: (projectId: string, runId: string) =>
    request<WorkflowJob[]>(`${runPath(projectId, runId)}/jobs`),
  runProgress: (projectId: string, runId: string) =>
    request<RunProgress>(`${runPath(projectId, runId)}/progress`),
  job: (projectId: string, runId: string, jobId: string) =>
    request<WorkflowJob>(`${runPath(projectId, runId)}/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (projectId: string, runId: string, jobId: string, expectedRevision: number) =>
    request<JobMutationResponse>(`${runPath(projectId, runId)}/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  evidenceAssessment: (projectId: string, runId: string) =>
    request<BrandEvidenceAssessment>(`${runPath(projectId, runId)}/evidence-assessment`),
  evidence: (projectId: string, runId: string, evidenceId: string) =>
    request<EvidenceSource>(`${runPath(projectId, runId)}/evidence/${encodeURIComponent(evidenceId)}`),
  contentStrategy: (projectId: string, runId: string) =>
    request<ContentStrategyResponse>(`${runPath(projectId, runId)}/content-strategy`),
  reviewRecommendations: (
    projectId: string,
    runId: string,
    expectedRevision: number,
    decisions: { task_id: string; decision: "accepted" | "rejected" }[],
  ) =>
    request<MeasurementRun>(`${runPath(projectId, runId)}/recommendation-review`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, decisions }),
    }),
  exportRun: (projectId: string, runId: string, expectedRevision: number) =>
    request<ExportResponse>(`${runPath(projectId, runId)}/exports`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  artifacts: (projectId: string, runId: string) =>
    request<ArtifactMetadata[]>(`${runPath(projectId, runId)}/artifacts`),
  artifact: (projectId: string, runId: string, artifactId: string) =>
    requestBinary(`${runPath(projectId, runId)}/artifacts/${encodeURIComponent(artifactId)}`),
  assessmentBundle: (projectId: string, runId: string, definitionVersion: number, measurementHash?: string) => {
    const query = new URLSearchParams({ definition_version: String(definitionVersion) });
    if (measurementHash) query.set("measurement_hash", measurementHash);
    return requestBinary(`${runPath(projectId, runId)}/evidence-assessment/download?${query}`);
  },
  contentStrategyBundle: (projectId: string, runId: string, measurementHash?: string) => {
    const query = new URLSearchParams();
    if (measurementHash) query.set("measurement_hash", measurementHash);
    const suffix = query.size ? `?${query}` : "";
    return requestBinary(`${runPath(projectId, runId)}/content-strategy/download${suffix}`);
  },
  conversations: (projectId: string) =>
    request<Conversation[]>(`${projectPath(projectId)}/conversations`),
  createConversation: (projectId: string, runId?: string) => {
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    return request<Conversation>(`${projectPath(projectId)}/conversations${query}`, { method: "POST" });
  },
  conversation: (projectId: string, conversationId: string) =>
    request<Conversation>(
      `${projectPath(projectId)}/conversations/${encodeURIComponent(conversationId)}`,
    ),
  sendMessage: (projectId: string, conversationId: string, body: SendMessageRequest) =>
    request<Conversation>(
      `${projectPath(projectId)}/conversations/${encodeURIComponent(conversationId)}/messages`,
      { method: "POST", body: JSON.stringify(body) },
    ),
};
