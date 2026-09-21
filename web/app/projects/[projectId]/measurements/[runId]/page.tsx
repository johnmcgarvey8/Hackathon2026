"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useProject } from "@/components/project-context";
import { RunProgressPanel } from "@/components/measurement/run-progress";
import { EvidenceDrawer, MeasurementResultsView } from "@/components/measurement/results/measurement-results";
import { useRunPolling } from "@/components/measurement/use-run-polling";
import { ScreenHeader } from "@/components/screen-header";
import { LoadingState, UnavailableState } from "@/components/status-state";
import { api, ApiError } from "@/lib/api";
import { actionAllowed, actionReason, pageUrl, runObjective } from "@/lib/measurement-runtime";
import type {
  ArtifactMetadata,
  BinaryArtifact,
  BrandEvidenceAssessment,
  ContentStrategyResponse,
  EvidenceSource,
  MeasurementRun,
  MeasurementCapacity,
  RunEvents,
  RunProgress,
  WorkflowJob,
} from "@/lib/types";

function displayDate(value?: string | null) {
  if (!value) return "Unknown";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function saveDownload(download: BinaryArtifact, fallback: string) {
  const url = URL.createObjectURL(download.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = download.filename || fallback;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function MeasurementRunPage() {
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
  const [capacity, setCapacity] = useState<MeasurementCapacity | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [recommendationDecisions, setRecommendationDecisions] = useState<Record<string, "accepted" | "rejected">>({});
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerError, setDrawerError] = useState<string | null>(null);
  const [drawerSource, setDrawerSource] = useState<EvidenceSource | null>(null);

  const loadAncillary = useCallback(async (currentRun: MeasurementRun) => {
    if (!project) return;
    const results = await Promise.allSettled([
      api.runProgress(project.project_id, currentRun.run_id),
      api.runJobs(project.project_id, currentRun.run_id),
      api.runEvents(project.project_id, currentRun.run_id),
      api.evidenceAssessment(project.project_id, currentRun.run_id),
      api.contentStrategy(project.project_id, currentRun.run_id),
      api.artifacts(project.project_id, currentRun.run_id),
      api.measurementCapacity(project.project_id),
    ]);
    if (results[0].status === "fulfilled") setProgress(results[0].value);
    if (results[1].status === "fulfilled") setJobs(results[1].value);
    if (results[2].status === "fulfilled") setEvents(results[2].value.events);
    if (results[3].status === "fulfilled") setAssessment(results[3].value);
    if (results[4].status === "fulfilled") setStrategy(results[4].value);
    if (results[5].status === "fulfilled") setArtifacts(results[5].value);
    if (results[6].status === "fulfilled") setCapacity(results[6].value);
  }, [project]);

  const refresh = useCallback(async () => {
    if (!project) return;
    const saved = await api.run(project.project_id, params.runId);
    setRun(saved);
    await loadAncillary(saved);
  }, [loadAncillary, params.runId, project]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    refresh()
      .catch((requestError) => {
        if (active) setError(requestError instanceof ApiError ? requestError.message : "The saved run is unavailable.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  useRunPolling(run, () => refresh());

  const actions = run?.available_actions || {};
  const cancelAction = actions.cancel;
  const reviewAction = actions.review_recommendations;
  const exportAction = actions.export;
  const discussAction = actions.discuss;
  const cancellableJob = useMemo(
    () => jobs.find((job) => ["queued", "leased"].includes(job.state))
      || (typeof cancelAction === "object" && cancelAction?.job_id
        ? jobs.find((job) => job.job_id === cancelAction.job_id) : undefined),
    [cancelAction, jobs],
  );

  if (!project) return null;

  const mutation = async (label: string, action: () => Promise<MeasurementRun>) => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const updated = await action();
      setRun(updated);
      await loadAncillary(updated);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 409) {
        await refresh().catch(() => undefined);
        setNotice("The saved revision changed. Latest server state was reloaded.");
      } else {
        setError(requestError instanceof ApiError ? requestError.message : `${label} failed.`);
      }
    } finally {
      setBusy(null);
    }
  };

  const cancel = () => mutation("Cancellation", async () => {
    if (!cancellableJob) throw new Error("No saved cancellable job is available.");
    return (await api.cancelJob(project.project_id, run!.run_id, cancellableJob.job_id, run!.revision)).run;
  });

  const reviewRecommendations = () => mutation("Recommendation review", () =>
    api.reviewRecommendations(
      project.project_id,
      params.runId,
      run!.revision,
      (run!.recommendations?.tasks || []).map((task) => ({
        task_id: task.task_id,
        decision: recommendationDecisions[task.task_id] || "rejected",
      })),
    ));

  const exportRun = () => mutation("Export", async () => {
    const response = await api.exportRun(project.project_id, params.runId, run!.revision);
    setArtifacts((items) => [response.artifact, ...items.filter((item) => item.artifact_id !== response.artifact.artifact_id)]);
    return response.run;
  });

  const discuss = async () => {
    if (!run) return;
    setBusy("Conversation creation");
    setError(null);
    try {
      const conversation = await api.createConversation(project.project_id, run.run_id);
      router.push(`/projects/${project.project_id}/chat?conversation=${encodeURIComponent(conversation.conversation_id)}`);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "A run-bound conversation could not be created.");
      setBusy(null);
    }
  };

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

  const downloadArtifact = async (artifact: ArtifactMetadata) => {
    setBusy("Artifact download");
    try {
      saveDownload(await api.artifact(project.project_id, params.runId, artifact.artifact_id), `geo-${params.runId}.zip`);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "The artifact could not be downloaded.");
    } finally {
      setBusy(null);
    }
  };

  const downloadAssessment = async () => {
    const version = assessment?.brand_definition?.definition_version;
    if (!version) return;
    setBusy("Assessment download");
    try {
      const measurementHash = typeof assessment.assessment?.measurement_hash === "string"
        ? assessment.assessment.measurement_hash : undefined;
      saveDownload(
        await api.assessmentBundle(project.project_id, params.runId, version, measurementHash),
        `geo-assessment-${params.runId}.zip`,
      );
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "The assessment bundle could not be downloaded.");
    } finally {
      setBusy(null);
    }
  };

  const downloadStrategy = async () => {
    setBusy("Strategy download");
    try {
      const measurementHash = typeof strategy?.report?.measurement_hash === "string"
        ? strategy.report.measurement_hash : undefined;
      saveDownload(
        await api.contentStrategyBundle(project.project_id, params.runId, measurementHash),
        `geo-content-strategy-${params.runId}.zip`,
      );
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "The content strategy bundle could not be downloaded.");
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <section className="screen"><LoadingState label="Loading saved measurement" /></section>;
  if (error && !run) return <section className="screen"><UnavailableState title="Measurement unavailable" message={error} /></section>;
  if (!run) return null;

  const runActive = ["preparing", "queued", "evaluating", "recommending"].includes(run.state.toLowerCase());

  return (
    <section className="screen measurement-run">
      <ScreenHeader
        eyebrow="Saved project measurement"
        title={runObjective(run)}
        description={`${pageUrl(run)} · revision ${run.revision}`}
        actions={<>
          <button className="button" type="button" disabled={busy !== null} onClick={() => void refresh()}>Reload saved state</button>
          <button className="button primary" type="button" disabled={!actionAllowed(discussAction) || busy !== null} title={actionReason(discussAction) || undefined} onClick={() => void discuss()}>Discuss this run</button>
        </>}
      />
      {error && <UnavailableState title="Action unavailable" message={error} compact />}
      {notice && <div className="notice" role="status">{notice}</div>}
      {!notice && runActive && (
        <div className="notice" role="status">
          This live measurement is running automatically. WebIQ retrieval, Foundry analysis, evaluation, and recommendations will advance without additional approval steps.
        </div>
      )}

      <div className="grid two run-overview">
        <section className="card">
          <div className="card-heading"><h2>Run state</h2><span className="pill blue">{run.state}</span></div>
          <dl className="scope-grid">
            <div><dt>Project</dt><dd>{project.name}</dd></div>
            <div><dt>Exact page</dt><dd>{pageUrl(run)}</dd></div>
            <div><dt>Locale</dt><dd>{run.brief?.locale || project.default_locale}</dd></div>
            <div><dt>Execution</dt><dd>Live WebIQ + Foundry</dd></div>
            <div><dt>Query binding</dt><dd>{run.approval_hash ? "Saved automatically" : "Pending preparation"}</dd></div>
            <div><dt>Latest job</dt><dd>{run.latest_job ? `${run.latest_job.job_type} · ${run.latest_job.state}` : "None"}</dd></div>
            <div><dt>Last saved</dt><dd>{displayDate(run.updated_at || run.created_at)}</dd></div>
            <div><dt>Live capacity</dt><dd>{capacity?.unlimited ? "No application limit" : "Provider availability applies"}</dd></div>
          </dl>
        </section>
        <section className="card">
          <div className="card-heading"><h2>Durable progress</h2><span className="pill">{progress?.job_state || "No active job"}</span></div>
          <RunProgressPanel progress={progress} />
        </section>
      </div>

      {actionAllowed(cancelAction) && (
        <section className="card">
          <div className="card-heading"><h2>Run control</h2><span className="pill amber">Optional</span></div>
          <p>Leave this page open or return later. The saved worker continues independently.</p>
          <button className="button" type="button" disabled={!cancellableJob || busy !== null} title={actionReason(cancelAction) || undefined} onClick={() => void cancel()}>Cancel live measurement</button>
        </section>
      )}

      <MeasurementResultsView run={run} assessment={assessment} strategy={strategy} onOpenEvidence={(evidenceId) => void openEvidence(evidenceId)} />

      {run.recommendations?.tasks.length && !run.recommendation_review ? (
        <section className="card">
          <div className="card-heading"><h2>Human recommendation review</h2><span className="pill amber">No publishing permission</span></div>
          {run.recommendations.tasks.map((task) => (
            <label className="review-row" key={task.task_id}><strong>{task.title}</strong><select value={recommendationDecisions[task.task_id] || "rejected"} onChange={(event) => setRecommendationDecisions((items) => ({ ...items, [task.task_id]: event.target.value as "accepted" | "rejected" }))}><option value="accepted">Accept for follow-up</option><option value="rejected">Reject</option></select></label>
          ))}
          <button className="button" type="button" disabled={!actionAllowed(reviewAction) || busy !== null} title={actionReason(reviewAction) || undefined} onClick={() => void reviewRecommendations()}>Save human review</button>
        </section>
      ) : null}

      <section className="card">
        <div className="card-heading"><h2>Artifacts and audit</h2><button className="button" type="button" disabled={!actionAllowed(exportAction) || busy !== null} title={actionReason(exportAction) || undefined} onClick={() => void exportRun()}>Create deterministic export</button></div>
        <div className="button-row">
          {artifacts.map((artifact) => <button className="button" type="button" key={artifact.artifact_id} disabled={busy !== null} onClick={() => void downloadArtifact(artifact)}>Download {artifact.artifact_id} ({Math.ceil(artifact.size / 1024)} KB)</button>)}
          {assessment?.assessment && assessment.brand_definition && <button className="button" type="button" disabled={busy !== null} onClick={() => void downloadAssessment()}>Download brand assessment</button>}
          {strategy?.status === "ready" && <button className="button" type="button" disabled={busy !== null} onClick={() => void downloadStrategy()}>Download content strategy</button>}
        </div>
        <details><summary>Saved events ({events.length})</summary><ol className="event-list">{events.map((event) => <li key={event.sequence}><strong>{event.event_type}</strong><span>{displayDate(event.occurred_at)}</span></li>)}</ol></details>
      </section>

      {drawerOpen && <EvidenceDrawer source={drawerSource} loading={drawerLoading} error={drawerError} onClose={() => setDrawerOpen(false)} />}
    </section>
  );
}
