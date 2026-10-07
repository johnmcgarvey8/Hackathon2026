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
  competitor_domains: string[];
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
  competitor_domains: string[];
  default_locale: string;
  active_goal: string | null;
  colour: string;
}

export interface UpdateProjectRequest extends CreateProjectRequest {
  expected_revision: number;
}

export interface CompetitorSummary {
  status: "not-configured" | "unavailable" | "none-found" | "grounding-found" | "cited";
  configured_domains: string[];
  grounding_domains: string[];
  grounding_query_ids: string[];
  grounding_source_count: number;
  cited_domains: string[];
  cited_profile_ids: string[];
  citation_count: number;
  completed_grounding_queries: number;
  intended_grounding_queries: number;
  completed_answers: number;
  intended_answers: number;
}

export interface MeasurementBriefRequest {
  url: string;
  audience: string;
  goal: string;
  locale: string;
  raw_goal?: string;
  goal_summary_operation_id?: string;
}

export interface MeasurementGoalSummaryRequest {
  raw_goal: string;
  url: string;
  audience?: string;
  desired_outcome?: string;
  target_kind?: "page" | "domain";
  idempotency_key: string;
}

export interface MeasurementGoalSummary {
  operation_id: string;
  input_hash: string;
  summary: string;
  fallback_used: boolean;
  provider_response_id: string | null;
  usage: Record<string, number>;
  error_code: string | null;
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
  mission?: "functional-planning" | "functional-constraint" | "transition-to-discovery" | "emotive-discovery" | "decision-validation" | null;
  moment?: "before-journey" | "early-journey" | "mid-journey" | "inspiration" | "point-of-decision" | null;
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
  evidence_type?: "grounding-citation";
  query_id?: string;
  grounding_query?: string;
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
  agent_call?: {
    role: SpecialistAgentRole;
    provider_mode: SpecialistProviderMode;
    provider_response_id?: string | null;
    agent_name?: string | null;
    agent_version?: string | null;
    model?: string | null;
  } | null;
}

export type SpecialistAgentRole = "grounding-query" | "llm-survey" | "recommendations";
export type SpecialistProviderMode = "project-agent" | "environment-agent" | "baseline-provider";

export interface AgentStageRecord {
  role: SpecialistAgentRole;
  status: "not-requested" | "queued" | "running" | "completed" | "failed" | "skipped";
  provider_mode: SpecialistProviderMode;
  job_id: string | null;
  input_hash: string;
  output_hash: string | null;
  provider_response_id: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  binding?: {
    project_endpoint: string;
    agent_name: string;
    agent_version: string;
  } | null;
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
  retry_recommendations?: AvailableAction;
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
  competitor_domains?: string[];
  competitor_summary?: CompetitorSummary;
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
  agent_stages?: AgentStageRecord[];
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
  job_type: "prepare" | "evaluate" | "recover-evaluators" | "agent-stage";
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
  job_type: WorkflowJob["job_type"] | null;
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
    queries: {
      query_id: string;
      grounding_query: string;
      status: "completed" | "error" | "missing";
      brand_status: "matched" | "ambiguous" | "absent" | "unknown" | "unconfigured";
      sources: {
        evidence_id: string;
        title: string | null;
        url: string;
        brand: {
          status: "matched" | "ambiguous" | "absent" | "unconfigured";
        };
        competitor_domain: string | null;
      }[];
    }[];
    answers: {
      query_id: string;
      profile_id: string;
      status: "completed" | "error" | "missing";
      brand: {
        status: "matched" | "ambiguous" | "absent" | "unknown" | "unconfigured";
        matches: unknown[];
      };
      competitor_source_count: number;
      competitor_cited_count: number;
      competitor_domains: string[];
      competitor_cited_domains: string[];
      [key: string]: unknown;
    }[];
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

export type GeoEvidenceType =
  | "measurement-run"
  | "grounding-query"
  | "grounding-citation"
  | "test-answer";

export interface ChatCitation {
  source_class: SourceClass;
  geo_evidence_type?: GeoEvidenceType | null;
  source_id: string;
  title: string;
  url: string | null;
  query_id?: string | null;
  brand_name?: string | null;
  brand_status?: "matched" | "ambiguous" | "absent" | "unknown" | "unconfigured" | null;
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
  workflow_id: string;
  status: "collecting" | "awaiting-confirmation" | "preparing" | "evaluating" | "completed" | "failed";
  target_kind: "page" | "domain" | null;
  url: string | null;
  goal: string | null;
  audience: string | null;
  desired_outcome: string | null;
  pending_field: "target" | "target-kind" | "goal" | "audience" | "desired-outcome" | "confirmation" | null;
  objective: string | null;
  run_id: string | null;
  source_idempotency_key: string | null;
  error: string | null;
}

export interface Conversation {
  schema_version: "geo-project-conversation/v1" | "geo-project-conversation/v2";
  conversation_id: string;
  project_id: string;
  run_id: string | null;
  linked_run_ids: string[];
  comparison_run_ids: string[];
  revision: number;
  title: string;
  turns: ConversationTurn[];
  measurement_workflow?: MeasurementWorkflow | null;
  measurement_workflows?: MeasurementWorkflow[];
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
