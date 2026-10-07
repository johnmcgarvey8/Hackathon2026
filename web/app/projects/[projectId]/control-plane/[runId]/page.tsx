"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useProject } from "@/components/project-context";
import { MeasurementSectionNav } from "@/components/measurement/measurement-section-nav";
import { RunProgressPanel } from "@/components/measurement/run-progress";
import { EvidenceDrawer, MeasurementResultsView } from "@/components/measurement/results/measurement-results";
import { useRunPolling } from "@/components/measurement/use-run-polling";
import { ScreenHeader } from "@/components/screen-header";
import { LoadingState, UnavailableState } from "@/components/status-state";
import { api, ApiError } from "@/lib/api";
import { isRunActive, pageUrl, runObjective } from "@/lib/measurement-runtime";
import type {
  ArtifactMetadata,
  BinaryArtifact,
  BrandEvidenceAssessment,
  ContentStrategyResponse,
  EvidenceSource,
  MeasurementRun,
  RunEvents,
  RunProgress,
  WorkflowJob,
} from "@/lib/types";

function displayDate(value?: string | null) {
  if (!value) return "Unknown";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function saveDownload(download: BinaryArtifact, fallback: string) {
  const url = URL.createObjectURL(download.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = download.filename || fallback;
  anchor.click();
  URL.revokeObjectURL(url);
}

function competitorSummaryText(run: MeasurementRun) {
  const summary = run.competitor_summary;
  if (!summary || summary.status === "not-configured") {
    return "No competitor domains were configured when this run was created.";
  }
  if (summary.status === "unavailable") {
    return `Tracking ${summary.configured_domains.join(", ")}. Saved measurement results are not available yet.`;
  }
  if (summary.status === "none-found") {
    return `No saved grounding sources matched ${summary.configured_domains.join(", ")}.`;
  }
  const grounding = `${summary.grounding_source_count} grounding ${summary.grounding_source_count === 1 ? "source" : "sources"} across ${summary.grounding_query_ids.length} ${summary.grounding_query_ids.length === 1 ? "query" : "queries"}`;
  if (summary.status === "cited") {
    return `${grounding}; ${summary.citation_count} LLM ${summary.citation_count === 1 ? "citation" : "citations"} referenced competitor sources.`;
  }
  return `${grounding}; no LLM survey answer cited those sources.`;
}

export default function ControlPlaneRunPage() {
  const { project } = useProject();
  const params = useParams<{ runId: string }>();
  const router = useRouter();
  const [run, setRun] = useState<MeasurementRun | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [jobs, setJobs] = useState<WorkflowJob[]>([]);
  const [events, setEvents] = useState<RunEvents["events"]>([]);
  const [assessment, setAssessment] = useState<BrandEvidenceAssessment | null>(null);
  const [strategy, setStrategy] = useState<ContentStrategyResponse | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerError, setDrawerError] = useState<string | null>(null);
  const [drawerSource, setDrawerSource] = useState<EvidenceSource | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!project) return;
    const savedRun = await api.run(project.project_id, params.runId);
    setRun(savedRun);
    const results = await Promise.allSettled([
      api.runProgress(project.project_id, params.runId),
      api.runJobs(project.project_id, params.runId),
      api.runEvents(project.project_id, params.runId),
      api.evidenceAssessment(project.project_id, params.runId),
      api.contentStrategy(project.project_id, params.runId),
      api.artifacts(project.project_id, params.runId),
    ]);
    if (results[0].status === "fulfilled") setProgress(results[0].value);
    if (results[1].status === "fulfilled") setJobs(results[1].value);
    if (results[2].status === "fulfilled") setEvents(results[2].value.events);
    if (results[3].status === "fulfilled") setAssessment(results[3].value);
    if (results[4].status === "fulfilled") setStrategy(results[4].value);
    if (results[5].status === "fulfilled") setArtifacts(results[5].value);
  }, [params.runId, project]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    refresh()
      .catch((requestError) => {
        if (active) {
          setError(requestError instanceof ApiError ? requestError.message : "The saved run is unavailable.");
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  useRunPolling(run, refresh);

  if (!project) return null;

  const openEvidence = async (evidenceId: string) => {
    setDrawerOpen(true);
    setDrawerLoading(true);
    setDrawerError(null);
    setDrawerSource(null);
    try {
      setDrawerSource(await api.evidence(project.project_id, params.runId, evidenceId));
    } catch (requestError) {
      setDrawerError(requestError instanceof ApiError ? requestError.message : "The saved evidence is unavailable.");
    } finally {
      setDrawerLoading(false);
    }
  };

  const download = async (request: () => Promise<BinaryArtifact>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      saveDownload(await request(), fallback);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "The saved download is unavailable.");
    } finally {
      setBusy(false);
    }
  };

  const deleteRun = async () => {
    if (!run || isRunActive(run) || busy || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteRun(project.project_id, run.run_id);
      router.push(`/projects/${project.project_id}/control-plane`);
    } catch (requestError) {
      setDeleteError(requestError instanceof ApiError ? requestError.message : "The measurement run could not be deleted.");
      setDeleting(false);
      setConfirmingDelete(false);
    }
  };

  if (loading) return <section className="screen"><LoadingState label="Loading read-only run detail" /></section>;
  if (error && !run) return <section className="screen"><UnavailableState title="Run unavailable" message={error} /></section>;
  if (!run) return null;

  return (
    <section className="screen measurement-run">
      <ScreenHeader
        eyebrow="Read-only control plane"
        title="Measurement run"
        description={`Revision ${run.revision} · last saved ${displayDate(run.updated_at || run.created_at)}`}
        backLink={{ href: `/projects/${project.project_id}/control-plane`, label: "Back to Control Plane" }}
      />

      <div className="measurement-run-layout">
        <div className="measurement-run-content">
          <section className="card run-brief" id="brief">
            <div className="card-heading"><h2>Run brief</h2><span className="pill blue">{run.state}</span></div>
            <dl className="scope-grid run-brief-grid">
              <div><dt>Goal</dt><dd>{runObjective(run)}</dd></div>
              <div><dt>Measured page</dt><dd>{pageUrl(run)}</dd></div>
              <div><dt>Competitor visibility</dt><dd>{competitorSummaryText(run)}</dd></div>
            </dl>
          </section>
          {error && <UnavailableState title="Saved data unavailable" message={error} compact />}

          <MeasurementResultsView
            run={run}
            assessment={assessment}
            strategy={strategy}
            showLimitations={false}
            onOpenEvidence={(evidenceId) => void openEvidence(evidenceId)}
          />

          <section className="card">
            <div className="card-heading"><h2>Saved downloads and audit</h2><span className="pill">GET only</span></div>
            <div className="button-row">
              {artifacts.map((artifact) => (
                <button
                  className="button"
                  type="button"
                  key={artifact.artifact_id}
                  disabled={busy}
                  onClick={() => void download(
                    () => api.artifact(project.project_id, params.runId, artifact.artifact_id),
                    `geo-${params.runId}.zip`,
                  )}
                >
                  Download {artifact.artifact_id} ({Math.ceil(artifact.size / 1024)} KB)
                </button>
              ))}
              {assessment?.assessment && assessment.brand_definition && (
                <button
                  className="button"
                  type="button"
                  disabled={busy}
                  onClick={() => void download(
                    () => api.assessmentBundle(
                      project.project_id,
                      params.runId,
                      assessment.brand_definition!.definition_version,
                      typeof assessment.assessment?.measurement_hash === "string"
                        ? assessment.assessment.measurement_hash : undefined,
                    ),
                    `geo-assessment-${params.runId}.zip`,
                  )}
                >
                  Download brand assessment
                </button>
              )}
              {strategy?.status === "ready" && (
                <button
                  className="button"
                  type="button"
                  disabled={busy}
                  onClick={() => void download(
                    () => api.contentStrategyBundle(
                      project.project_id,
                      params.runId,
                      typeof strategy.report?.measurement_hash === "string"
                        ? strategy.report.measurement_hash : undefined,
                    ),
                    `geo-content-strategy-${params.runId}.zip`,
                  )}
                >
                  Download content strategy
                </button>
              )}
            </div>
            {artifacts.length === 0 && <p className="small muted">No previously created artifacts are saved.</p>}
            <details>
              <summary>Saved jobs ({jobs.length})</summary>
              <ol className="event-list">{jobs.map((job) => <li key={job.job_id}><strong>{job.job_type} · {job.state}</strong><span>{displayDate(job.updated_at)}</span></li>)}</ol>
            </details>
            <details>
              <summary>Saved events ({events.length})</summary>
              <ol className="event-list">{events.map((event) => <li key={event.sequence}><strong>{event.event_type}</strong><span>{displayDate(event.occurred_at)}</span></li>)}</ol>
            </details>
          </section>

          <details className="card troubleshooting-panel">
            <summary>
              <span><strong>Troubleshooting details</strong><small>Run metadata, saved job state, and durable operation progress</small></span>
              <span className="pill blue">{run.state}</span>
            </summary>
            <div className="grid two run-overview troubleshooting-content">
              <section>
                <div className="card-heading"><h2>Run state</h2><span className="pill blue">{run.state}</span></div>
                <dl className="scope-grid">
                  <div><dt>Project</dt><dd>{project.name}</dd></div>
                  <div><dt>Exact page</dt><dd>{pageUrl(run)}</dd></div>
                  <div><dt>Run ID</dt><dd><code>{run.run_id}</code></dd></div>
                  <div><dt>Latest job</dt><dd>{run.latest_job ? `${run.latest_job.job_type} · ${run.latest_job.state}` : "None"}</dd></div>
                  <div><dt>Last saved</dt><dd>{displayDate(run.updated_at || run.created_at)}</dd></div>
                </dl>
              </section>
              <section>
                <div className="card-heading"><h2>Durable progress</h2><span className="pill">{progress?.job_state || "No active job"}</span></div>
                <RunProgressPanel progress={progress} active={isRunActive(run)} />
              </section>
            </div>
          </details>

          <section className="card danger-zone" id="danger-zone">
            <div className="card-heading">
              <div>
                <p className="eyebrow danger-eyebrow">Danger zone</p>
                <h2>Delete this measurement run</h2>
              </div>
              <span className="pill red">Permanent</span>
            </div>
            <p className="small muted">
              Deleting removes the run, its saved evidence, scores, jobs, and exported files permanently.
              This cannot be undone. Conversations that reference this run are retained, with references to it removed.
            </p>
            {isRunActive(run) && (
              <p className="small muted">This run is still executing. Wait for it to finish or cancel it before deleting.</p>
            )}
            {deleteError && <p className="form-error">{deleteError}</p>}
            {confirmingDelete ? (
              <div className="button-row">
                <button
                  className="button danger"
                  type="button"
                  disabled={deleting || isRunActive(run)}
                  onClick={() => void deleteRun()}
                >
                  {deleting ? "Deleting..." : "Yes, delete permanently"}
                </button>
                <button
                  className="button"
                  type="button"
                  disabled={deleting}
                  onClick={() => setConfirmingDelete(false)}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="button-row">
                <button
                  className="button danger"
                  type="button"
                  disabled={busy || isRunActive(run)}
                  title={isRunActive(run) ? "Active measurement runs must finish or be cancelled first." : undefined}
                  onClick={() => { setDeleteError(null); setConfirmingDelete(true); }}
                >
                  Delete measurement run
                </button>
              </div>
            )}
          </section>
        </div>
        <MeasurementSectionNav />
      </div>

      {drawerOpen && <EvidenceDrawer source={drawerSource} loading={drawerLoading} error={drawerError} onClose={() => setDrawerOpen(false)} />}
    </section>
  );
}
