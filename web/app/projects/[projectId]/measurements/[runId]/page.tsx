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
  QueryPair,
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
  const [queryDraft, setQueryDraft] = useState<QueryPair[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmPreparation, setConfirmPreparation] = useState(false);
  const [confirmEvaluation, setConfirmEvaluation] = useState(false);
  const [includeRecommendations, setIncludeRecommendations] = useState(true);
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
    ]);
    if (results[0].status === "fulfilled") setProgress(results[0].value);
    if (results[1].status === "fulfilled") setJobs(results[1].value);
    if (results[2].status === "fulfilled") setEvents(results[2].value.events);
    if (results[3].status === "fulfilled") setAssessment(results[3].value);
    if (results[4].status === "fulfilled") setStrategy(results[4].value);
    if (results[5].status === "fulfilled") setArtifacts(results[5].value);
  }, [project]);

  const refresh = useCallback(async (preserveDraft = false) => {
    if (!project) return;
    const saved = await api.run(project.project_id, params.runId);
    setRun(saved);
    if (!preserveDraft) setQueryDraft(saved.inputs?.query_plan.queries || []);
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
  const prepareAction = actions.prepare;
  const reviseAction = actions.revise_queries;
  const approveAction = actions.approve_queries || actions.approve;
  const startAction = actions.start;
  const recoverEvaluatorsAction = actions.recover_evaluators;
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
      setQueryDraft(updated.inputs?.query_plan.queries || queryDraft);
      await loadAncillary(updated);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 409) {
        await refresh(true).catch(() => undefined);
        setNotice("The saved revision changed. Latest server state was reloaded and your query draft was preserved. Review differences, then reapply explicitly.");
      } else {
        setError(requestError instanceof ApiError ? requestError.message : `${label} failed.`);
      }
    } finally {
      setBusy(null);
    }
  };

  const prepare = () => mutation("Preparation", async () => {
    const response = await api.prepareRun(project.project_id, params.runId, run!.revision);
    setConfirmPreparation(false);
    setProgress(null);
    return response.run;
  });

  const reviseQueries = () => mutation("Query revision", () =>
    api.reviseQueries(project.project_id, params.runId, run!.revision, queryDraft));

  const approveQueries = () => mutation("Query approval", () =>
    api.approveQueries(project.project_id, params.runId, run!.revision, run!.approval_hash!));

  const start = () => mutation("Evaluation start", async () => {
    const response = await api.startRun(project.project_id, params.runId, run!.revision, includeRecommendations);
    setConfirmEvaluation(false);
    setProgress(null);
    return response.run;
  });

  const recoverEvaluators = () => mutation("Evaluator recovery", async () => {
    const response = await api.recoverEvaluators(project.project_id, params.runId, run!.revision);
    setConfirmEvaluation(false);
    setProgress(null);
    return response.run;
  });

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

  const estimates = typeof prepareAction === "object" ? prepareAction.operation_estimates : run.operation_estimates;
  const preparationEstimates = estimates?.preparation;
  const hasQueries = queryDraft.length > 0;
  const evaluationReady = Boolean(run.approval && actionAllowed(startAction));

  return (
    <section className="screen measurement-run">
      <ScreenHeader
        eyebrow="Saved project measurement"
        title={runObjective(run)}
        description={`${pageUrl(run)} · revision ${run.revision}`}
        actions={<>
          <button className="button" type="button" disabled={busy !== null} onClick={() => void refresh(true)}>Reload saved state</button>
          <button className="button primary" type="button" disabled={!actionAllowed(discussAction) || busy !== null} title={actionReason(discussAction) || undefined} onClick={() => void discuss()}>Discuss this run</button>
        </>}
      />
      {error && <UnavailableState title="Action unavailable" message={error} compact />}
      {notice && <div className="notice" role="status">{notice}</div>}
      {!notice && evaluationReady && (
        <div className="notice" role="status">
          Queries approved. Confirm the evaluation provider calls in step 3, then select <strong>Start approved run</strong>.
        </div>
      )}

      <div className="grid two run-overview">
        <section className="card">
          <div className="card-heading"><h2>Run state</h2><span className="pill blue">{run.state}</span></div>
          <dl className="scope-grid">
            <div><dt>Project</dt><dd>{project.name}</dd></div>
            <div><dt>Exact page</dt><dd>{pageUrl(run)}</dd></div>
            <div><dt>Locale</dt><dd>{run.brief?.locale || project.default_locale}</dd></div>
            <div><dt>Execution policy</dt><dd>{run.policy_mode || "Unavailable"}</dd></div>
            <div><dt>Approval hash</dt><dd><code>{run.approval_hash || "Not generated"}</code></dd></div>
            <div><dt>Latest job</dt><dd>{run.latest_job ? `${run.latest_job.job_type} · ${run.latest_job.state}` : "None"}</dd></div>
            <div><dt>Last saved</dt><dd>{displayDate(run.updated_at || run.created_at)}</dd></div>
          </dl>
        </section>
        <section className="card">
          <div className="card-heading"><h2>Durable progress</h2><span className="pill">{progress?.job_state || "No active job"}</span></div>
          <RunProgressPanel progress={progress} />
        </section>
      </div>

      <section className="card workflow-actions">
        <div className="card-heading"><h2>Governed actions</h2><span className="small muted">Availability comes from FastAPI</span></div>
        <div className="action-grid">
          <article>
            <h3>1. Prepare page and queries</h3>
            <p>Preparation may retrieve the exact page, analyse it, and generate paired queries.</p>
            {preparationEstimates && <p className="small muted">Estimated operations: {Object.entries(preparationEstimates).map(([name, count]) => `${name.replaceAll("_", " ")} ${count}`).join(", ")}</p>}
            <label className="confirmation"><input type="checkbox" checked={confirmPreparation} onChange={(event) => setConfirmPreparation(event.target.checked)} /> I confirm preparation provider calls.</label>
            <button className="button primary" type="button" disabled={!actionAllowed(prepareAction) || !confirmPreparation || busy !== null} title={actionReason(prepareAction) || undefined} onClick={() => void prepare()}>Prepare measurement</button>
          </article>
          <article>
            <h3>2. Approve exact queries</h3>
            <p>Save any edits first. Approval binds the current revision to the displayed hash.</p>
            <button className="button" type="button" disabled={!actionAllowed(approveAction) || !run.approval_hash || busy !== null} title={actionReason(approveAction) || undefined} onClick={() => void approveQueries()}>Approve queries and unlock evaluation</button>
          </article>
          <article>
            <h3>3. Start evaluation</h3>
            <label className="confirmation"><input type="checkbox" checked={confirmEvaluation} onChange={(event) => setConfirmEvaluation(event.target.checked)} /> I confirm evaluation provider calls.</label>
            <label className="confirmation"><input type="checkbox" checked={includeRecommendations} onChange={(event) => setIncludeRecommendations(event.target.checked)} /> Include optional recommendations.</label>
            <button className="button primary" type="button" disabled={!actionAllowed(startAction) || !confirmEvaluation || busy !== null} title={actionReason(startAction) || undefined} onClick={() => void start()}>Start approved run</button>
            {actionAllowed(recoverEvaluatorsAction) && (
              <button className="button primary" type="button" disabled={!confirmEvaluation || busy !== null} title={actionReason(recoverEvaluatorsAction) || undefined} onClick={() => void recoverEvaluators()}>Retry failed evaluators using saved searches</button>
            )}
          </article>
          <article>
            <h3>Cancel active job</h3>
            <p>Cancellation records unresolved and not-attempted operations. It never restarts work.</p>
            <button className="button" type="button" disabled={!actionAllowed(cancelAction) || !cancellableJob || busy !== null} title={actionReason(cancelAction) || undefined} onClick={() => void cancel()}>Cancel saved job</button>
          </article>
        </div>
      </section>

      {hasQueries && (
        <section className="card query-editor">
          <div className="card-heading"><h2>Exact query review</h2><span className="pill">{queryDraft.length} saved query pairs</span></div>
          <div className="query-editor-list">
            {queryDraft.map((query, index) => (
              <article key={query.query_id}>
                <div className="card-heading"><h3>{query.query_id}</h3><label className="confirmation"><input type="checkbox" checked={query.branded} onChange={(event) => setQueryDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, branded: event.target.checked } : item))} /> Branded</label></div>
                <div className="form-grid">
                  <label className="form-field"><span>priority</span><select value={query.priority} onChange={(event) => setQueryDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, priority: Number(event.target.value) } : item))}>{[1, 2, 3, 4, 5].map((priority) => <option value={priority} key={priority}>{priority}</option>)}</select></label>
                  {(["chat_query", "grounding_query", "intent", "rationale"] as const).map((field) => (
                    <label className="form-field" key={field}><span>{field.replaceAll("_", " ")}</span><textarea maxLength={500} value={query[field]} onChange={(event) => setQueryDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: event.target.value } : item))} /></label>
                  ))}
                </div>
                <p className="small muted">Retained page evidence: {query.evidence.map((item) => `${item.evidence_id}: ${item.quote}`).join(" | ")}</p>
              </article>
            ))}
          </div>
          <button className="button" type="button" disabled={!actionAllowed(reviseAction) || busy !== null} title={actionReason(reviseAction) || undefined} onClick={() => void reviseQueries()}>Save query revision</button>
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
