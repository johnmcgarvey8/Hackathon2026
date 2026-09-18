"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { MeasurementRun } from "@/lib/types";
import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";
import { LoadingState, UnavailableState } from "@/components/status-state";

function displayDate(value: unknown) {
  if (typeof value !== "string") return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function objective(run: MeasurementRun) {
  return run.brief?.objective || "Project measurement";
}

export default function ControlPlanePage() {
  const { project } = useProject();
  const [runs, setRuns] = useState<MeasurementRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!project) return;
    setLoading(true);
    api.runs(project.project_id)
      .then(setRuns)
      .catch((requestError) => setError(requestError instanceof ApiError ? requestError.message : "Run data is unavailable."))
      .finally(() => setLoading(false));
  }, [project]);

  if (!project) return null;
  const activeRuns = runs.filter((run) => ["preparing", "queued", "evaluating", "recommending"].includes(run.state.toLowerCase()));

  return (
    <section className="screen">
      <ScreenHeader eyebrow="Project operations" title="Control Plane" description="Follow project execution and review measurement history." />
      {loading ? <LoadingState label="Loading project runs" /> : error ? (
        <UnavailableState title="Control Plane unavailable" message={error} />
      ) : (
        <div className="stack">
          {activeRuns.length > 0 && (
            <section className="card accent">
              <div className="card-heading"><div><p className="eyebrow">Active execution</p><h2>{objective(activeRuns[0])}</h2></div><span className="pill blue">{activeRuns[0].state}</span></div>
              <div className="progress-track" aria-label="Run in progress"><span style={{ width: "55%" }} /></div>
              <p className="small muted">Run {activeRuns[0].run_id} is executing in the FastAPI workflow.</p>
            </section>
          )}
          <section className="card">
            <div className="card-heading"><h2>Project history</h2><span className="small muted">{runs.length} runs</span></div>
            {runs.length === 0 ? <UnavailableState title="No run history" message="No measurement runs are currently bound to this project." compact /> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Run</th><th>Goal</th><th>Status</th><th>Revision</th><th>Updated</th></tr></thead>
                  <tbody>{runs.map((run) => (
                    <tr key={run.run_id}>
                      <td><code>{run.run_id}</code></td>
                      <td>{objective(run)}</td>
                      <td><span className={`pill ${run.state.toLowerCase() === "completed" ? "green" : "blue"}`}>{run.state}</span></td>
                      <td>{run.revision}</td>
                      <td>{displayDate(run.updated_at || run.created_at)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
