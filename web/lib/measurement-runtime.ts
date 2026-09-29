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
