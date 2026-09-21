import { operationLabel, operationTotals } from "@/lib/measurement-runtime";
import type { RunProgress } from "@/lib/types";

export function RunProgressPanel({ progress }: { progress: RunProgress | null }) {
  if (!progress) {
    return <p className="small muted">Saved operation progress is not available yet.</p>;
  }
  const totals = operationTotals(progress);

  return (
    <div className="run-progress">
      <div className="progress-summary">
        <div>
          <strong>{totals.resolved} of {totals.planned} operations resolved</strong>
          <small>{progress.current_operation ? operationLabel(progress.current_operation) : "No operation in flight"}</small>
        </div>
        <span>{totals.percentage}%</span>
      </div>
      <div className="progress-track" aria-label={`${totals.percentage}% of saved operations resolved`}>
        <span style={{ width: `${totals.percentage}%` }} />
      </div>
      <div className="operation-grid">
        {progress.operations.map((operation) => (
          <article className="operation-card" key={operation.operation_type}>
            <div><strong>{operationLabel(operation)}</strong>{operation.optional && <span className="pill">Optional</span>}</div>
            <span>{operation.completed}/{operation.planned} complete</span>
            {(operation.failed > 0 || operation.unresolved > 0 || operation.not_attempted > 0) && (
              <small>
                {operation.failed > 0 && `${operation.failed} failed. `}
                {operation.unresolved > 0 && `${operation.unresolved} unresolved. `}
                {operation.not_attempted > 0 && `${operation.not_attempted} not attempted.`}
              </small>
            )}
          </article>
        ))}
      </div>
      {progress.error_code && <p className="form-error">Saved failure code: {progress.error_code}</p>}
    </div>
  );
}
