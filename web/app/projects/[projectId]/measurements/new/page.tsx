"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";
import { api, ApiError } from "@/lib/api";
import type { MeasurementCapacity } from "@/lib/types";

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
      const response = await api.createMeasurement(project.project_id, {
        url: page.trim(),
        audience: effectiveGoal,
        goal: effectiveGoal,
        locale: project.default_locale,
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
            <input type="url" required placeholder={`https://${project.primary_domain}/exact-page`} value={page} onChange={(event) => setPage(event.target.value)} />
            <small>Subdomains are accepted. FastAPI performs the authoritative validation before any live call.</small>
          </label>
          <label className="form-field full">
            <span>What do you want to learn?</span>
            <textarea required maxLength={1000} value={objective} onChange={(event) => setObjective(event.target.value)} />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="button-row"><button className="button primary" type="submit" disabled={submitting}>{submitting ? "Starting live measurement..." : "Start live measurement"}</button></div>
        </form>
      </div>
    </section>
  );
}
