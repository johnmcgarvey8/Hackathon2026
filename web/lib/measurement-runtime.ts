import type {
  AvailableAction,
  MeasurementRun,
  OperationProgress,
  RunProgress,
} from "./types";

const activeStates = new Set(["preparing", "queued", "evaluating", "recommending"]);
const terminalOrReviewStates = new Set([
  "draft",
  "awaiting-query-approval",
  "ready",
  "partial",
  "failed",
  "needs-review",
  "cancelled",
  "exported",
]);

export function actionAllowed(action: AvailableAction | undefined): boolean {
  return typeof action === "boolean" ? action : action?.allowed === true;
}

export function actionReason(action: AvailableAction | undefined): string | null {
  return typeof action === "object" && action ? action.reason || null : null;
}

export function isRunActive(run: MeasurementRun): boolean {
  return activeStates.has(run.state.toLowerCase());
}

export type MeasurementResultStage =
  | "queries"
  | "evidence"
  | "answers"
  | "scores"
  | "recommendations";

export type MeasurementOperationState = "queued" | "running" | "completed" | "failed";

export function isMeasurementResultPending(
  run: MeasurementRun,
  stage: MeasurementResultStage,
): boolean {
  if (!isRunActive(run)) return false;

  if (stage === "recommendations") {
    const recommendationStage = (run.agent_stages || []).find(
      (item) => item.role === "recommendations",
    );
    if (recommendationStage?.status === "queued" || recommendationStage?.status === "running") {
      return true;
    }
    if (["completed", "failed", "skipped"].includes(recommendationStage?.status || "")) {
      return false;
    }
    return run.result_availability?.recommendations !== true && !run.recommendations;
  }

  const availabilityKey = {
    queries: "query_plan",
    evidence: "evidence",
    answers: "answers",
    scores: "citations",
  }[stage] as "query_plan" | "evidence" | "answers" | "citations";

  return run.result_availability?.[availabilityKey] !== true;
}

export function measurementOperationState(
  operation: OperationProgress,
): MeasurementOperationState {
  if (operation.in_flight > 0) return "running";
  if (operation.failed > 0 && operation.completed + operation.failed >= operation.planned) {
    return "failed";
  }
  if (operation.planned > 0 && operation.completed + operation.failed >= operation.planned) {
    return "completed";
  }
  return "queued";
}

export function shouldPollRun(run: MeasurementRun | null): boolean {
  if (!run) return false;
  const state = run.state.toLowerCase();
  return activeStates.has(state) && !terminalOrReviewStates.has(state);
}

export function operationTotals(progress: RunProgress | null) {
  const operations = progress?.operations || [];
  const planned = operations.reduce((sum, operation) => sum + operation.planned, 0);
  const resolved = operations.reduce(
    (sum, operation) => sum + operation.completed + operation.failed,
    0,
  );
  return {
    planned,
    resolved,
    percentage: planned > 0 ? Math.min(100, Math.round((resolved / planned) * 100)) : 0,
  };
}

export function operationLabel(operation: OperationProgress | string | null): string {
  const type = typeof operation === "string" ? operation : operation?.operation_type;
  return {
    "webiq-browse": "Page retrieval",
    "page-analysis-model": "Page analysis",
    "paired-query-plan": "Query planning",
    "missions-query-plan": "Mission and moment query generation",
    "paired-query-plan-fallback": "Fallback query planning",
    "webiq-search": "WebIQ search",
    "profile-evaluator": "Model evaluation",
    "recommendation-model": "Recommendations",
  }[type || ""] || type?.replaceAll("-", " ") || "Waiting";
}

export function pageUrl(run: MeasurementRun): string {
  const value = run.brief?.url;
  if (typeof value === "string") return value;
  return value?.value || run.inputs?.snapshot.url || "Page unavailable";
}

export function runObjective(run: MeasurementRun): string {
  return run.brief?.goal || run.brief?.objective || "Project measurement";
}
