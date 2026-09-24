"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { isRunActive, operationLabel, operationTotals, pageUrl, runObjective } from "@/lib/measurement-runtime";
import type { MeasurementRun, RunProgress } from "@/lib/types";
import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";
import { LoadingState, UnavailableState } from "@/components/status-state";

function displayDate(value: unknown) {
  if (typeof value !== "string") return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export default function ControlPlanePage() {
  const { project } = useProject();
  const [runs, setRuns] = useState<MeasurementRun[]>([]);
  const [progress, setProgress] = useState<Record<string, RunProgress>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pollCount = useRef(0);

  const load = useCallback(async () => {
    if (!project) return;
    const savedRuns = await api.runs(project.project_id);
    setRuns(savedRuns);
    const active = savedRuns.filter(isRunActive);
    const results = await Promise.allSettled(active.map((run) => api.runProgress(project.project_id, run.run_id)));
    setProgress((current) => {
      const next = { ...current };
      results.forEach((result, index) => {
        if (result.status === "fulfilled") next[active[index].run_id] = result.value;
      });
      return next;
    });
  }, [project]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    load()
      .catch((requestError) => {
        if (active) setError(requestError instanceof ApiError ? requestError.message : "Run data is unavailable.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [load]);

  const activeRuns = useMemo(() => runs.filter(isRunActive), [runs]);
  const activeRunIds = useMemo(
    () => activeRuns.map((run) => run.run_id).join(","),
    [activeRuns],
  );

  useEffect(() => {
    pollCount.current = 0;
    if (activeRuns.length === 0) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (stopped || pollCount.current >= 120) return;
      if (document.visibilityState === "visible") {
        pollCount.current += 1;
        await load().catch(() => undefined);
      }
      timer = setTimeout(tick, 4000);
    };
    timer = setTimeout(tick, 4000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [activeRunIds, activeRuns.length, load]);

  if (!project) return null;

  return (
    <section className="screen">
      <ScreenHeader
        eyebrow="Project operations"
        title="Measurement Control Plane"
        description="Read-only visibility into measurements started from Chat or the secondary manual workflow."
      />
      {error && <UnavailableState title="Control Plane unavailable" message={error} compact />}
      {loading ? <LoadingState label="Loading project runs" /> : (
        <div className="stack">
          {activeRuns.map((run) => {
            const savedProgress = progress[run.run_id] || run.progress || null;
            const totals = operationTotals(savedProgress);
            return (
              <section className="card accent" key={run.run_id}>
                <div className="card-heading"><div><p className="eyebrow">Active execution</p><h2>{runObjective(run)}</h2></div><span className="pill blue">{run.state}</span></div>
                <p className="small muted">{pageUrl(run)}</p>
                <div className="progress-summary"><span>{savedProgress?.current_operation ? operationLabel(savedProgress.current_operation) : "Waiting for saved operation"}</span><strong>{totals.resolved}/{totals.planned}</strong></div>
                <div className="progress-track" aria-label={`${totals.percentage}% of operations resolved`}><span style={{ width: `${totals.percentage}%` }} /></div>
                {savedProgress?.operations.map((operation) => (
                  <span className="operation-inline" key={operation.operation_type}>{operationLabel(operation)}: {operation.completed}/{operation.planned}{operation.failed ? `, ${operation.failed} failed` : ""}{operation.not_attempted ? `, ${operation.not_attempted} not attempted` : ""}</span>
                ))}
                {savedProgress?.error_code && <p className="form-error">Failure code: {savedProgress.error_code}</p>}
                <div className="button-row">
                  <Link className="button" href={`/projects/${project.project_id}/control-plane/${run.run_id}`}>Open read-only detail</Link>
                </div>
              </section>
            );
          })}
          <section className="card">
            <div className="card-heading"><h2>Project history</h2><span className="small muted">{runs.length} runs</span></div>
            {runs.length === 0 ? <UnavailableState title="No run history" message="Start a measurement in Chat to populate the Control Plane." compact /> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Page and objective</th><th>Status</th><th>Current operation</th><th>Revision</th><th>Updated</th><th>Actions</th></tr></thead>
                  <tbody>{runs.map((run) => {
                    const savedProgress = progress[run.run_id] || run.progress || null;
                    return (
                      <tr key={run.run_id}>
                        <td><strong>{pageUrl(run)}</strong><small className="table-subtitle">{runObjective(run)}</small></td>
                        <td><span className={`pill ${["ready", "exported"].includes(run.state.toLowerCase()) ? "green" : run.state.toLowerCase() === "failed" ? "red" : "blue"}`}>{run.state}</span></td>
                        <td>{savedProgress?.current_operation ? operationLabel(savedProgress.current_operation) : "None"}</td>
                        <td>{run.revision}</td>
                        <td>{displayDate(run.updated_at || run.created_at)}</td>
                        <td><Link className="button ghost" href={`/projects/${project.project_id}/control-plane/${run.run_id}`}>{run.measurement ? "View results" : "View run"}</Link></td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
