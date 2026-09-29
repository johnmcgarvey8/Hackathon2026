"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";
import { api, ApiError } from "@/lib/api";
import type { MeasurementCapacity, MeasurementGoalSummary } from "@/lib/types";

function belongsToProject(value: string, domains: string[]) {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return false;
    const host = url.hostname.toLowerCase();
    return domains.some((domain) => host === domain || host.endsWith(`.${domain}`));
  } catch {
    return false;
  }
}

export default function NewMeasurementPage() {
  const { project } = useProject();
  const router = useRouter();
  const [page, setPage] = useState("");
  const [objective, setObjective] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capacity, setCapacity] = useState<MeasurementCapacity | null>(null);
  const [goalSummary, setGoalSummary] = useState<MeasurementGoalSummary | null>(null);
  const [confirmedGoal, setConfirmedGoal] = useState("");

  useEffect(() => {
    if (!project) return;
    let active = true;
    setObjective((current) => current || project.active_goal || "");
    api.measurementCapacity(project.project_id)
      .then((saved) => { if (active) setCapacity(saved); })
      .catch(() => { if (active) setCapacity(null); });
    return () => { active = false; };
  }, [project]);

  if (!project) return null;
  const effectiveGoal = objective.trim();

  const resetSummary = () => {
    setGoalSummary(null);
    setConfirmedGoal("");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!belongsToProject(page, project.domains)) {
      setError(`Enter an exact HTTP(S) page within ${project.domains.join(", ")}.`);
      return;
    }
    if (!effectiveGoal) {
      setError("Add a measurement objective because this project has no active goal.");
      return;
    }
    setSubmitting(true);
    try {
      if (!goalSummary) {
        const summary = await api.summarizeMeasurementGoal(project.project_id, {
          raw_goal: effectiveGoal,
          url: page.trim(),
          audience: effectiveGoal,
          target_kind: "page",
          idempotency_key: crypto.randomUUID(),
        });
        setGoalSummary(summary);
        setConfirmedGoal(summary.summary);
        return;
      }
      if (!confirmedGoal.trim()) {
        setError("Confirm a concise measurement goal before starting the run.");
        return;
      }
      const response = await api.createMeasurement(project.project_id, {
        url: page.trim(),
        audience: effectiveGoal,
        goal: confirmedGoal.trim(),
        locale: project.default_locale,
        raw_goal: effectiveGoal,
        goal_summary_operation_id: goalSummary.operation_id,
      });
      router.push(`/projects/${project.project_id}/measurements/${response.run.run_id}`);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "The measurement could not be created.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="screen">
      <ScreenHeader eyebrow="Live project measurement" title="Measure a page" description="Enter the page and goal once. WebIQ and Foundry complete the live measurement automatically." />
      <div className="grid two measurement-create">
        <section className="card">
          <div className="card-heading"><h2>Authoritative project scope</h2><span className="pill green">Active project</span></div>
          <dl className="scope-grid">
            <div><dt>Brand</dt><dd>{project.name}</dd></div>
            <div><dt>Primary domain</dt><dd>{project.primary_domain}</dd></div>
            <div><dt>Allowed domains</dt><dd>{project.domains.join(", ")}</dd></div>
            <div><dt>Locale</dt><dd>{project.default_locale}</dd></div>
            <div><dt>Goal</dt><dd>{project.active_goal || "Set for this measurement below"}</dd></div>
          </dl>
          <p className="small muted">The run stays within this Project. No synthetic provider or fixture fallback is available.</p>
          <p className="small muted">{capacity?.unlimited ? "No application run limit. WebIQ and Foundry provider quotas still apply." : "Live provider availability will be validated when the run starts."}</p>
        </section>
        <form className="card project-form" onSubmit={(event) => void submit(event)}>
          <label className="form-field full">
            <span>Exact in-scope page</span>
            <input type="url" required placeholder={`https://${project.primary_domain}/exact-page`} value={page} onChange={(event) => { setPage(event.target.value); resetSummary(); }} />
            <small>Subdomains are accepted. FastAPI performs the authoritative validation before any live call.</small>
          </label>
          <label className="form-field full">
            <span>What do you want to learn?</span>
            <textarea required maxLength={1000} value={objective} onChange={(event) => { setObjective(event.target.value); resetSummary(); }} />
          </label>
          {goalSummary && (
            <section className="summary-preview" aria-live="polite">
              <p className="eyebrow">Confirm measurement goal</p>
              <p className="small muted">
                {goalSummary.fallback_used
                  ? "Foundry could not summarise the goal, so your original input is shown."
                  : "Foundry proposed a concise goal. Edit it if needed before starting the run."}
              </p>
              <label className="form-field full">
                <span>Concise run goal</span>
                <textarea required maxLength={1000} value={confirmedGoal} onChange={(event) => setConfirmedGoal(event.target.value)} />
              </label>
              <details>
                <summary>Original input</summary>
                <p className="small muted">{effectiveGoal}</p>
              </details>
            </section>
          )}
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="button-row">
            {goalSummary && <button className="button ghost" type="button" disabled={submitting} onClick={resetSummary}>Edit inputs</button>}
            <button className="button primary" type="submit" disabled={submitting}>
              {submitting
                ? goalSummary ? "Starting live measurement..." : "Summarising goal..."
                : goalSummary ? "Confirm and start live measurement" : "Review measurement goal"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
