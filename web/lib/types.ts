export type FoundryStatus = "configured-unverified" | "manual-setup-required";

export interface ChatStatus {
  mode: "foundry" | "unavailable";
  can_send: boolean;
  organisational_context_available: boolean;
  detail: string;
  agent?: {
    name: string;
    version: string;
    scope: "shared-default" | "project";
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

export interface MeasurementBriefRequest {
  url: string;
  audience: string;
  goal: string;
  locale: string;
}

export interface MeasurementBrief {
  url: string | { value?: string };
  audience?: string;
  goal?: string;
  objective?: string;
  locale?: string;
}

export interface EvidenceQuote {
  evidence_id: string;
  quote: string;
}

export interface QueryPair {
  query_id: string;
  priority: number;
  rationale: string;
  intent: string;
  branded: boolean;
  chat_query: string;
  grounding_query: string;
  evidence: EvidenceQuote[];
}

export interface QueryPlan {
  queries: QueryPair[];
}

export interface EvidenceSource {
  evidence_id: string;
  url: string;
  excerpt: string;
  provenance: "synthetic" | "recorded" | "live";
  title: string;
  returned_position: number | null;
  provider_trace_id?: string | null;
  crawled_at?: string | null;
  last_updated_at?: string | null;
}

export interface RetrievalResult {
  query_id: string;
  grounding_query: string;
  provenance: "synthetic" | "recorded" | "live";
  status: "completed" | "error";
  sources: EvidenceSource[];
  error: string | null;
  provider_trace_id?: string | null;
  retrieved_at?: string;
}

export interface EvaluatedAnswer {
  query_id: string;
  profile_id: string;
  provenance: "synthetic" | "recorded" | "live";
  status: "completed" | "error";
  answer: string;
  citation_ids: string[];
  sources: EvidenceSource[];
  error: string | null;
  model?: string | null;
  response_id?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

export interface MeasurementInputs {
  schema_version?: string;
  brief: MeasurementBrief;
  snapshot: {
    url: string;
    title: string;
    content?: string;
    provenance: "synthetic" | "recorded" | "live";
    captured_at?: string;
  };
  query_plan: QueryPlan;
  profiles: {
    profile_id: string;
    provider?: string;
    deployment?: string;
    prompt_version?: string;
    simulation?: boolean;
  }[];
  policy_hash?: string;
  method_version?: string;
  retrieval_mode?: string;
  approval_hash?: string;
}

export interface CitationScore {
  score: number | null;
  numerator: number;
  denominator: number;
  intended: number;
  errors: number;
  missing: number;
  coverage: number;
  provisional?: boolean;
}

export interface CitationAnswerFinding {
  query_id: string;
  profile_id: string;
  status: "completed" | "error" | "missing";
  exact_page_cited: boolean | null;
  same_domain_citation_count: number;
  other_page_citation_count: number;
  unsupported_citation_ids: string[];
  raw_url_mentioned: boolean;
}

export interface CitationScores {
  method_version?: string;
  provisional?: boolean;
  overall?: CitationScore;
  by_profile?: Record<string, CitationScore>;
  by_query?: Record<string, CitationScore>;
  by_query_type?: Record<string, CitationScore>;
  common_completed_query_ids?: string[];
  comparable_by_profile?: Record<string, CitationScore>;
  retrieval?: CitationScore & {
    failures?: { query_id: string; error: string | null }[];
    by_query?: Record<string, {
      status: "completed" | "error" | "missing";
      target_returned: boolean | null;
      returned_position: number | null;
      error: string | null;
    }>;
  };
  by_answer?: CitationAnswerFinding[];
}

export interface RecommendationTask {
  task_id: string;
  priority: number;
  query_id: string;
  target_section: string;
  title: string;
  proposed_change: string;
  rationale: string;
  confidence: "low" | "medium";
  verification: string;
  page_evidence: EvidenceQuote[];
  comparison_evidence: EvidenceQuote[];
  status: "draft";
  requires_human_approval: true;
  publish_permission: false;
}

export interface RecommendationReport {
  status: "completed" | "insufficient-evidence";
  reason: string;
  limitations: string;
  tasks: RecommendationTask[];
  approval_hash?: string;
  measurement_hash?: string;
  method_version?: string;
  prompt_version?: string;
}

export interface RecommendationReview {
  approval_hash: string;
  measurement_hash: string;
  recommendation_hash: string;
  decisions: { task_id: string; decision: "accepted" | "rejected" }[];
  reviewed_at: string;
  publish_permission: false;
}

export interface MeasurementResults {
  schema_version?: string;
  inputs: MeasurementInputs;
  retrievals: RetrievalResult[];
  results: EvaluatedAnswer[];
}

export type AvailableAction =
  | boolean
  | {
      allowed: boolean;
      reason?: string | null;
      confirmation_required?: boolean;
      expected_revision?: number;
      operation_estimates?: OperationEstimates;
      job_id?: string | null;
    };

export interface RunAvailableActions {
  prepare?: AvailableAction;
  revise_queries?: AvailableAction;
  approve_queries?: AvailableAction;
  approve?: AvailableAction;
  start?: AvailableAction;
  cancel?: AvailableAction;
  review_recommendations?: AvailableAction;
  export?: AvailableAction;
  discuss?: AvailableAction;
  [key: string]: AvailableAction | undefined;
}

export interface ResultAvailability {
  query_plan?: boolean;
  evidence?: boolean;
  answers?: boolean;
  citations?: boolean;
  brand_presence?: boolean;
  recommendations?: boolean;
  artifacts?: boolean;
  [key: string]: boolean | undefined;
}

export interface OperationEstimates {
  preparation?: Record<string, number>;
  evaluation?: Record<string, number>;
  automatic_retries?: number;
  [key: string]: Record<string, number> | number | undefined;
}

export interface MeasurementRun {
  schema_version?: string;
  run_id: string;
  project_id?: string | null;
  state: string;
  revision: number;
  created_at?: string;
  updated_at?: string;
  brief?: MeasurementBrief | null;
  inputs?: MeasurementInputs | null;
  approval?: {
    revision: number;
    input_hash: string;
    approved_at: string;
  } | null;
  approval_hash?: string | null;
  measurement?: MeasurementResults | null;
  recommendations?: RecommendationReport | null;
  recommendation_review?: RecommendationReview | null;
  scores?: CitationScores | Record<string, number> | null;
  available_actions?: RunAvailableActions;
  result_availability?: ResultAvailability;
  operation_estimates?: OperationEstimates;
  policy_mode?: string | null;
  latest_job?: WorkflowJob | null;
  progress?: RunProgress | null;
  failure_code?: string | null;
  [key: string]: unknown;
}

export interface WorkflowJob {
  schema_version?: string;
  job_id: string;
  run_id: string;
  run_revision: number;
  job_type: "prepare" | "evaluate" | "recover-evaluators";
  state: "queued" | "leased" | "completed" | "failed" | "cancelled";
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface OperationProgress {
  operation_type: string;
  planned: number;
  completed: number;
  failed: number;
  in_flight: number;
  unresolved: number;
  not_attempted: number;
  optional: boolean;
}

export interface RunProgress {
  run_id: string;
  run_revision: number;
  run_state: string;
  job_id: string | null;
  job_type: "prepare" | "evaluate" | null;
  job_state: WorkflowJob["state"] | null;
  error_code: string | null;
  queued_at: string | null;
  last_activity_at: string;
  observed_at: string;
  current_operation: string | null;
  operations: OperationProgress[];
}

export interface RunEvents {
  run_id: string;
  revision: number;
  events: { sequence: number; event_type: string; occurred_at: string }[];
}

export interface BrandEvidenceAssessment {
  run_id: string;
  run_revision: number;
  status: string;
  brand_definition: {
    definition_version: number;
    definition_hash: string;
    definition: {
      name: string;
      aliases: { text: string; ambiguous: boolean }[];
      domains: string[];
    };
  } | null;
  assessment: {
    grounding: Record<string, unknown>;
    answer: Record<string, unknown>;
    queries: Record<string, unknown>[];
    answers: Record<string, unknown>[];
    limitations: string[];
    [key: string]: unknown;
  } | null;
  assessment_hash: string | null;
}

export interface ContentStrategyResponse {
  run_id: string;
  run_revision: number;
  status: string;
  report: {
    limitations?: string | string[];
    [key: string]: unknown;
  } | null;
}

export interface ArtifactMetadata {
  schema_version?: string;
  artifact_id: string;
  run_id: string;
  content_hash: string;
  media_type: string;
  size: number;
  created_at: string;
}

export interface JobMutationResponse {
  job: WorkflowJob;
  run: MeasurementRun;
}

export interface MeasurementCapacity {
  authorized_runs: number | null;
  consumed_runs: number;
  remaining_runs: number | null;
  exhausted: boolean;
  unlimited?: boolean;
  operations?: Record<string, {
    allowance: number;
    consumed: number;
    remaining: number;
  }>;
}

export interface AutomaticMeasurementResponse extends JobMutationResponse {
  capacity?: MeasurementCapacity;
}

export interface ExportResponse {
  artifact: ArtifactMetadata;
  run: MeasurementRun;
  download_url: string;
}

export interface BinaryArtifact {
  blob: Blob;
  mediaType: string;
  filename: string | null;
  etag: string | null;
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
  origin: "user" | "workflow";
  message: string;
  idempotency_key: string;
  expected_revision: number;
  use_organisational_context: boolean;
  status: "running" | "completed" | "failed";
  answer: string | null;
  error: string | null;
  citations: ChatCitation[];
  provider_response_id: string | null;
  mode: "mock" | "foundry" | "workflow" | null;
  created_at: string;
  completed_at: string | null;
}

export interface MeasurementWorkflow {
  status: "collecting" | "preparing" | "evaluating" | "completed" | "failed";
  url: string | null;
  objective: string | null;
  run_id: string | null;
  source_idempotency_key: string | null;
  error: string | null;
}

export interface Conversation {
  schema_version: "geo-project-conversation/v1";
  conversation_id: string;
  project_id: string;
  run_id: string | null;
  revision: number;
  title: string;
  turns: ConversationTurn[];
  measurement_workflow?: MeasurementWorkflow | null;
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
