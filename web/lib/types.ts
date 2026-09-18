export type FoundryStatus = "configured-unverified" | "manual-setup-required";

export interface ChatStatus {
  mode: "foundry" | "mock" | "unavailable";
  can_send: boolean;
  organisational_context_available: boolean;
  detail: string;
  agent?: {
    name: string;
    version: string;
    scope: "shared-default" | "project";
  };
  budget?: {
    limit: number;
    used: number;
    remaining: number;
  };
}

export interface Project {
  schema_version: "geo-project/v1";
  project_id: string;
  revision: number;
  name: string;
  primary_domain: string;
  additional_domains: string[];
  domains: string[];
  default_locale: string;
  active_goal: string | null;
  colour: string;
  initials: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
  run_count: number;
  active_run_count: number;
  latest_run: MeasurementRun | null;
  foundry_status: FoundryStatus;
}

export interface CreateProjectRequest {
  name: string;
  primary_domain: string;
  additional_domains: string[];
  default_locale: string;
  active_goal: string | null;
  colour: string;
}

export interface MeasurementRun {
  run_id: string;
  project_id?: string | null;
  state: string;
  revision: number;
  created_at?: string;
  updated_at?: string;
  brief?: {
    objective?: string;
    url?: string | { value?: string };
    locale?: string;
  } | null;
  scores?: Record<string, number> | null;
  approval_hash?: string | null;
  [key: string]: unknown;
}

export type SourceClass =
  | "geo-evidence"
  | "org-knowledge"
  | "work-context"
  | "model-knowledge";

export interface ChatCitation {
  source_class: SourceClass;
  source_id: string;
  title: string;
  url: string | null;
}

export interface ConversationTurn {
  sequence: number;
  message: string;
  idempotency_key: string;
  expected_revision: number;
  use_organisational_context: boolean;
  status: "running" | "completed" | "failed";
  answer: string | null;
  error: string | null;
  citations: ChatCitation[];
  provider_response_id: string | null;
  mode: "mock" | "foundry" | null;
  created_at: string;
  completed_at: string | null;
}

export interface Conversation {
  schema_version: "geo-project-conversation/v1";
  conversation_id: string;
  project_id: string;
  run_id: string | null;
  revision: number;
  title: string;
  turns: ConversationTurn[];
  created_at: string;
  updated_at: string;
}

export interface SendMessageRequest {
  message: string;
  expected_revision: number;
  idempotency_key: string;
  use_organisational_context: boolean;
}

export interface ApiErrorPayload {
  error: string;
  status?: number;
  detail?: unknown;
}
