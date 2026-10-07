import { MeasurementLoadingState } from "./measurement-loading-state";
import {
  measurementOperationState,
  operationLabel,
  operationTotals,
} from "@/lib/measurement-runtime";
import type { RunProgress } from "@/lib/types";

const operationStateLabels = {
  queued: "Queued",
  running: "Running",
  completed: "Complete",
  failed: "Failed",
};

export function RunProgressPanel({
  progress,
  active = false,
}: {
  progress: RunProgress | null;
  active?: boolean;
}) {
  if (!progress) {
    if (active) return <MeasurementLoadingState stage="progress" compact />;
    return <p className="small muted">Saved operation progress is not available yet.</p>;
  }
  const totals = operationTotals(progress);
  const progressInFlight = active && totals.resolved < totals.planned;

  return (
    <div className="run-progress">
      <div className="progress-summary">
        <div>
          <strong>{totals.resolved} of {totals.planned} operations resolved</strong>
          <small>{progress.current_operation ? operationLabel(progress.current_operation) : "No operation in flight"}</small>
        </div>
        <span>{totals.percentage}%</span>
      </div>
      <div
        className={`progress-track ${progressInFlight ? "in-progress" : ""}`}
        aria-label={`${totals.percentage}% of saved operations resolved`}
      >
        <span style={{ width: `${totals.percentage}%` }} />
      </div>
      {progress.operations.length === 0 && active ? (
        <MeasurementLoadingState
          stage="progress"
          detail={progress.current_operation
            ? `${operationLabel(progress.current_operation)} is starting.`
            : undefined}
          compact
        />
      ) : (
        <div className="operation-grid">
          {progress.operations.map((operation) => {
            const operationState = measurementOperationState(operation);
            return (
              <article
                className={`operation-card ${operationState}`}
                key={operation.operation_type}
              >
                <div className="operation-card-heading">
                  <span>
                    <strong>{operationLabel(operation)}</strong>
                    {operation.optional && <span className="pill">Optional</span>}
                  </span>
                  <span className={`operation-status ${operationState}`}>
                    {operationState === "running" && (
                      <span className="operation-spinner" aria-hidden="true" />
                    )}
                    {operationStateLabels[operationState]}
                  </span>
                </div>
                <span>{operation.completed}/{operation.planned} complete</span>
                {(operation.failed > 0 || operation.unresolved > 0 || operation.not_attempted > 0) && (
                  <small>
                    {operation.failed > 0 && `${operation.failed} failed. `}
                    {operation.unresolved > 0 && `${operation.unresolved} unresolved. `}
                    {operation.not_attempted > 0 && `${operation.not_attempted} not attempted.`}
                  </small>
                )}
              </article>
            );
          })}
        </div>
      )}
      {progress.error_code && <p className="form-error">Saved failure code: {progress.error_code}</p>}
    </div>
  );
}
