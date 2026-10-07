import type {
  BrandEvidenceAssessment,
  ChatCitation,
  CitationAnswerFinding,
  CitationScore,
  CitationScores,
  ContentStrategyResponse,
  EvidenceSource,
  EvaluatedAnswer,
  MeasurementRun,
} from "@/lib/types";
import { AssistantMarkdown } from "@/components/assistant-markdown";
import { MeasurementLoadingState } from "@/components/measurement/measurement-loading-state";
import { evidencePresentation } from "@/lib/evidence-presentation";
import { isMeasurementResultPending } from "@/lib/measurement-runtime";
import { surveyModelRoster } from "@/lib/survey-models";

function isCitationScores(value: MeasurementRun["scores"]): value is CitationScores {
  if (!value || typeof value !== "object") return false;
  return "by_answer" in value || ("overall" in value && typeof value.overall === "object");
}

function scoreText(score?: CitationScore) {
  if (!score || score.score === null) return "Unknown";
  return `${score.score}% (${score.numerator}/${score.denominator}, ${score.missing} missing)`;
}

function findingLabels(finding: CitationAnswerFinding) {
  if (finding.status !== "completed") return ["Unknown, answer unavailable"];
  const labels = [];
  if (finding.exact_page_cited) labels.push("Exact page cited");
  if (finding.same_domain_citation_count > 0) labels.push("Same-domain page cited");
  if (finding.other_page_citation_count > 0) labels.push("Other-domain page cited");
  if (finding.unsupported_citation_ids.length > 0) labels.push("Unsupported citation ID");
  if (finding.raw_url_mentioned) labels.push("Raw URL mention, not a citation");
  return labels.length ? labels : ["No citation"];
}

function groundingMatchPresentation(
  status: NonNullable<BrandEvidenceAssessment["assessment"]>["queries"][number]["brand_status"],
  brandName: string,
) {
  if (status === "matched") return { className: "green", label: `✓ ${brandName} found` };
  if (status === "ambiguous") return { className: "amber", label: `Possible ${brandName} match` };
  if (status === "absent") return { className: "", label: `${brandName} not found` };
  return { className: "", label: "Result unknown" };
}

function answerBrandPresentation(
  status: NonNullable<BrandEvidenceAssessment["assessment"]>["answers"][number]["brand"]["status"],
  brandName: string,
) {
  if (status === "matched") return { className: "green", label: `✓ ${brandName} mentioned` };
  if (status === "ambiguous") return { className: "amber", label: `Possible ${brandName} mention` };
  if (status === "absent") return { className: "", label: `${brandName} not mentioned` };
  if (status === "unconfigured") return { className: "", label: "Brand not configured" };
  return { className: "", label: "Brand result unknown" };
}

function providerAnswerCitations(answer: EvaluatedAnswer): ChatCitation[] {
  return answer.sources.map((source) => ({
    source_class: "geo-evidence",
    geo_evidence_type: "grounding-citation",
    source_id: source.evidence_id,
    title: source.title || source.url,
    url: source.url,
    query_id: source.query_id || answer.query_id,
  }));
}

export function MeasurementResultsView({
  run,
  assessment,
  strategy,
  showLimitations = true,
  onOpenEvidence,
}: {
  run: MeasurementRun;
  assessment: BrandEvidenceAssessment | null;
  strategy: ContentStrategyResponse | null;
  showLimitations?: boolean;
  onOpenEvidence: (evidenceId: string) => void;
}) {
  const measurement = run.measurement;
  const scores = isCitationScores(run.scores) ? run.scores : null;
  const queries = run.inputs?.query_plan.queries || [];
  const retrievals = measurement?.retrievals || [];
  const answers = measurement?.results || [];
  const surveyModels = surveyModelRoster(measurement?.inputs, answers);
  const modelByProfile = new Map(
    surveyModels.map((model) => [model.profileId, model]),
  );
  const queryIds = Array.from(new Set([
    ...queries.map((query) => query.query_id),
    ...retrievals.map((retrieval) => retrieval.query_id),
    ...answers.map((answer) => answer.query_id),
  ])).sort((left, right) => {
    const leftNumber = Number(left.replace(/^q-/, ""));
    const rightNumber = Number(right.replace(/^q-/, ""));
    return Number.isNaN(leftNumber) || Number.isNaN(rightNumber)
      ? left.localeCompare(right)
      : leftNumber - rightNumber;
  });
  const scenarioQueries = [...queries].sort((left, right) => left.priority - right.priority);
  const brandName = assessment?.brand_definition?.definition.name || "Brand";
  const groundingFindings = assessment?.assessment?.queries || [];
  const answerBrandFindings = assessment?.assessment?.answers || [];
  const hasNonLiveData = Boolean(
    (run.inputs && run.inputs.snapshot.provenance !== "live")
    || retrievals.some((retrieval) =>
      retrieval.provenance !== "live"
      || retrieval.sources.some((source) => source.provenance !== "live"))
    || answers.some((answer) =>
      answer.provenance !== "live"
      || answer.sources.some((source) => source.provenance !== "live")),
  );
  const limitations = [
    "Search evidence is limited to saved result packets and does not represent the full web.",
    "LLM provider responses and saved passages are untrusted evidence and may contain errors.",
    "Literal brand or domain presence does not establish sentiment, endorsement, causality, ranking, or proof of absence.",
    "Failed and missing outcomes remain unknown rather than being treated as zero.",
    ...(assessment?.assessment?.limitations || []),
    ...(run.recommendations?.limitations ? [run.recommendations.limitations] : []),
    ...(typeof strategy?.report?.limitations === "string"
      ? [strategy.report.limitations]
      : Array.isArray(strategy?.report?.limitations) ? strategy.report.limitations : []),
  ];
  const recommendationStage = (run.agent_stages || []).find(
    (stage) => stage.role === "recommendations",
  );
  const recommendationProvider = recommendationStage?.provider_mode === "baseline-provider"
    ? "Standard LLM provider"
    : recommendationStage?.provider_mode === "project-agent"
      ? "Project Recommendations Agent"
      : recommendationStage?.provider_mode === "environment-agent"
        ? "Default Recommendations Agent"
        : "Provider not recorded";
  const queriesPending = queryIds.length === 0 && isMeasurementResultPending(run, "queries");
  const evidencePending = isMeasurementResultPending(run, "evidence");
  const answersPending = answers.length === 0 && isMeasurementResultPending(run, "answers");
  const scoresPending = !scores && isMeasurementResultPending(run, "scores");
  const recommendationsPending = !run.recommendations
    && isMeasurementResultPending(run, "recommendations");

  if (hasNonLiveData) {
    return (
      <section className="card result-section">
        <div className="card-heading"><h2>Results blocked</h2><span className="pill red">Non-live provenance</span></div>
        <p>This web workspace displays only live WebIQ evidence and live LLM provider responses. This saved run contains non-live data and cannot be shown here.</p>
      </section>
    );
  }

  return (
    <div className="result-sections">
      <section className="card result-section" id="user-scenarios">
        <div className="card-heading"><h2>User scenarios</h2><span className="pill blue">Moments and missions simulation</span></div>
        <p className="muted">These scenarios model the user need, mission and moment sent to the LLM provider survey.</p>
        {queriesPending ? <MeasurementLoadingState stage="queries" /> : scenarioQueries.length === 0 ? <p className="muted">{run.latest_job?.state === "failed" ? "Preparation stopped before user scenarios could be saved." : "No user scenarios were saved for this run."}</p> : (
          <div className="measurement-query-list">
            {scenarioQueries.map((query) => (
              <details className="measurement-query-group" key={`scenario-${query.query_id}`}>
                <summary>
                  <span className="measurement-query-title">
                    <strong>{query.intent}</strong>
                    <span>{query.chat_query}</span>
                  </span>
                  <span className="measurement-query-counts">Priority {query.priority}</span>
                </summary>
                <div className="measurement-query-content">
                  <section className="measurement-query-section">
                    <div className="section-heading-inline">
                      <h3>Mission and moment</h3>
                      {query.mission && <span className="pill">{query.mission.replaceAll("-", " ")}</span>}
                      {query.moment && <span className="pill">{query.moment.replaceAll("-", " ")}</span>}
                      {query.branded && <span className="pill">Branded</span>}
                    </div>
                    <p><strong>User scenario:</strong> {query.chat_query}</p>
                    <p><strong>Rationale:</strong> {query.rationale}</p>
                    <small><strong>Page evidence used to design this scenario:</strong> {query.evidence.length
                      ? query.evidence.map((item) => `${item.evidence_id}: ${item.quote}`).join(" | ")
                      : "No exact page quote was retained."}</small>
                  </section>
                </div>
              </details>
            ))}
          </div>
        )}
      </section>

      <section className="card result-section" id="query-plan">
        <div className="card-heading"><h2>Grounding queries</h2><span className="pill">{queryIds.length} queries</span></div>
        <p className="muted">Each Web IQ query is shown with the grounding citations supplied to the LLM during inference and literal brand presence in the saved results.</p>
        {queriesPending ? <MeasurementLoadingState stage="queries" /> : queryIds.length === 0 ? <p className="muted">{run.latest_job?.state === "failed" ? "Preparation stopped before grounding queries could be saved." : "No grounding queries were saved for this run."}</p> : (
          <div className="measurement-query-list">
            {queryIds.map((queryId) => {
              const query = queries.find((item) => item.query_id === queryId);
              const retrieval = retrievals.find((item) => item.query_id === queryId);
              const brandFinding = groundingFindings.find((item) => item.query_id === queryId);
              const brandPresentation = brandFinding
                ? groundingMatchPresentation(brandFinding.brand_status, brandName)
                : { className: "", label: assessment?.brand_definition ? "Brand result unknown" : "Brand not configured" };
              const brandSources = new Map(
                (brandFinding?.sources || []).map((source) => [source.evidence_id, source.brand.status]),
              );
              const competitorSources = new Map(
                (brandFinding?.sources || []).map((source) => [source.evidence_id, source.competitor_domain]),
              );
              const groundingQuery = query?.grounding_query || retrieval?.grounding_query || queryId;
              return (
                <details className="measurement-query-group" key={queryId}>
                  <summary>
                    <span className="measurement-query-title">
                      <strong>{groundingQuery}</strong>
                      {query?.intent && <span>{query.intent}</span>}
                    </span>
                    <span className="measurement-query-counts">
                      <span>{retrieval?.sources.length || 0} grounding {retrieval?.sources.length === 1 ? "citation" : "citations"}</span>
                      <span className={`pill ${brandPresentation.className}`}>{brandPresentation.label}</span>
                    </span>
                  </summary>
                  <div className="measurement-query-content">
                    <section className="measurement-query-section">
                      <div className="section-heading-inline">
                        <h3>Grounding supplied to the LLM</h3>
                        <span className={`pill ${retrieval?.status === "completed" ? "green" : retrieval ? "red" : ""}`}>
                          {retrieval?.status || "Unavailable"}
                        </span>
                      </div>
                      {retrieval?.error && <p className="form-error">{retrieval.error}</p>}
                      {!retrieval && evidencePending ? (
                        <MeasurementLoadingState stage="evidence" compact />
                      ) : !retrieval || retrieval.sources.length === 0 ? <p className="small muted">No saved grounding citations are available.</p> : retrieval.sources.map((source) => (
                        <button className="evidence-row" type="button" key={source.evidence_id} onClick={() => onOpenEvidence(source.evidence_id)}>
                          <span>
                            <span className="evidence-kind-row">
                              <span className="pill evidence-kind">Grounding citation</span>
                              {brandSources.get(source.evidence_id) === "matched" && <span className="pill green">{brandName} found</span>}
                              {brandSources.get(source.evidence_id) === "ambiguous" && <span className="pill amber">Possible {brandName} match</span>}
                              {competitorSources.get(source.evidence_id) && (
                                <span className="pill red">Competitor: {competitorSources.get(source.evidence_id)}</span>
                              )}
                            </span>
                            <strong>{source.title || source.url}</strong>
                            <small>{source.url}</small>
                            <small>Returned position {source.returned_position ?? "unknown"} · {source.provenance} provenance</small>
                          </span>
                          <span>View citation</span>
                        </button>
                      ))}
                    </section>

                  </div>
                </details>
              );
            })}
          </div>
        )}
      </section>

      <section className="card result-section" id="model-answers">
        <div className="card-heading"><h2>LLM Provider Survey</h2><span className={`pill ${answersPending ? "blue" : "green"}`}>{answersPending ? "Running" : `Post-inference · ${answers.length} responses`}</span></div>
        <p className="muted">These are the post-inference provider responses generated after the saved WebIQ grounding citations were supplied to the LLM. Each response links back to its survey prompt and reported citation IDs.</p>
        {answersPending ? <MeasurementLoadingState stage="answers" /> : answers.length === 0 ? <p className="muted">No saved LLM provider responses are available.</p> : (
          <div className="provider-survey-list">
            {answers.map((answer) => {
              const query = queries.find((item) => item.query_id === answer.query_id);
              const brandFinding = answerBrandFindings.find(
                (item) => item.query_id === answer.query_id && item.profile_id === answer.profile_id,
              );
              const brandPresentation = brandFinding
                ? answerBrandPresentation(brandFinding.brand.status, brandName)
                : { className: "", label: assessment?.brand_definition ? "Brand result unknown" : "Brand not configured" };
              const citedCompetitors = brandFinding?.competitor_cited_domains || [];
              return (
                <details className="provider-survey-response" key={`${answer.query_id}-${answer.profile_id}`}>
                  <summary>
                    <span className="measurement-query-title">
                      <strong>{query?.chat_query || "Prompt unavailable"}</strong>
                      <span>{modelByProfile.get(answer.profile_id)?.label || answer.model || answer.profile_id}</span>
                    </span>
                    <span className="measurement-query-counts">
                      <span className={`pill ${brandPresentation.className}`}>{brandPresentation.label}</span>
                      {citedCompetitors.length > 0 && (
                        <span className="pill red">Cited competitor: {citedCompetitors.join(", ")}</span>
                      )}
                    </span>
                  </summary>
                  <div className="provider-survey-content">
                    {answer.status === "completed" ? (
                      <AssistantMarkdown
                        content={answer.answer}
                        citations={providerAnswerCitations(answer)}
                        onCitationClick={(citation) => onOpenEvidence(citation.source_id)}
                      />
                    ) : <p className="form-error">{answer.error || "Response unavailable."}</p>}
                    <small>Citation IDs: {answer.citation_ids.length ? answer.citation_ids.join(", ") : "None"}</small>
                  </div>
                </details>
              );
            })}
          </div>
        )}
      </section>

      <section className="card result-section" id="citation-performance">
        <div className="card-heading"><h2>Citation performance</h2><span className="pill">{scoresPending ? "Calculating" : scores?.provisional ? "Provisional" : "Saved result"}</span></div>
        <p className="muted">
          Surveyed models are identified by friendly profile name and the exact configured or provider-returned model.
        </p>
        {scoresPending ? <MeasurementLoadingState stage="scores" /> : (
          <>
            <div className="grid three">
              {surveyModels.map((model) => (
                <article className="metric-card" key={model.profileId}>
                  <span>{model.label}</span>
                  <strong>{scoreText(scores?.by_profile?.[model.profileId])}</strong>
                  <small>{model.provider} · configured deployment {model.deployment}</small>
                </article>
              ))}
            </div>
            <div className="grid three">
              <article className="metric-card"><span>Exact-page citation</span><strong>{scoreText(scores?.overall)}</strong></article>
              <article className="metric-card"><span>Target returned by WebIQ</span><strong>{scoreText(scores?.retrieval)}</strong></article>
              <article className="metric-card"><span>Comparable queries</span><strong>{scores?.common_completed_query_ids?.length ?? "Unknown"}</strong></article>
            </div>
            <div className="finding-list">
              {(scores?.by_answer || []).map((finding) => (
                <div key={`${finding.query_id}-${finding.profile_id}`}>
                  <span>{finding.query_id} · {modelByProfile.get(finding.profile_id)?.label || finding.profile_id}</span><strong>{findingLabels(finding).join(" · ")}</strong>
                </div>
              ))}
            </div>
            {!scores && <p className="muted">Citation scores are unknown because no saved measurement results are available.</p>}
          </>
        )}
      </section>

      <section className="card result-section" id="recommendations">
        <div className="card-heading"><h2>Recommendations</h2><span className="pill">{recommendationStage?.status || run.recommendations?.status || "Not requested"}</span></div>
        <p className="small muted">{recommendationProvider}{recommendationStage?.binding ? ` · ${recommendationStage.binding.agent_name} v${recommendationStage.binding.agent_version}` : ""}</p>
        {recommendationsPending ? (
          <MeasurementLoadingState stage="recommendations" />
        ) : !run.recommendations ? (
          <p className="muted">
            {recommendationStage?.status === "failed"
              ? `Recommendation generation failed${recommendationStage.error_code ? ` (${recommendationStage.error_code})` : ""}. Measurement results remain available.`
              : "No recommendation report is saved."}
          </p>
        ) : (
          <>
            {run.recommendations.reason && <p>{run.recommendations.reason}</p>}
            {run.recommendations.tasks.map((task) => (
              <article className="recommendation-card" key={task.task_id}>
                <div><span className="pill blue">Priority {task.priority}</span><strong>{task.title}</strong><span className="pill">{task.confidence} confidence</span></div>
                <p><strong>Proposed change:</strong> {task.proposed_change}</p>
                <p><strong>Evidence-bound rationale:</strong> {task.rationale}</p>
                <p><strong>Verification:</strong> {task.verification}</p>
                <small>Human approval required. Publishing is not authorised.</small>
              </article>
            ))}
          </>
        )}
      </section>

      {showLimitations && (
        <section className="card result-section limitations" id="limitations">
          <div className="card-heading"><h2>Limitations and provenance</h2><span className="pill amber">Read before use</span></div>
          <ul>{Array.from(new Set(limitations)).map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
          <p className="small muted">Run {run.run_id}, revision {run.revision}. Evidence IDs and saved provenance remain attached to each source.</p>
        </section>
      )}
    </div>
  );
}

export function EvidenceDrawer({
  source,
  loading,
  error,
  onClose,
}: {
  source: EvidenceSource | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const presentation = evidencePresentation(source?.evidence_type || "grounding-citation");
  return (
    <>
      <button className="drawer-backdrop" aria-label="Close evidence" onClick={onClose} />
      <aside className="drawer open" aria-modal="true" role="dialog" aria-labelledby="evidence-title">
        <div className="drawer-header"><div><p className="eyebrow">WebIQ evidence</p><h2 id="evidence-title">Grounding citation</h2></div><button className="icon-button" type="button" aria-label="Close evidence" onClick={onClose}>×</button></div>
        <div className="drawer-content">
          {loading ? <p>Loading saved evidence...</p> : error ? <p className="form-error">{error}</p> : source && (
            <article className="source-detail">
              <span className={`pill evidence-kind ${presentation.pillClass}`}>{presentation.label}</span>
              <h3>{source.title || "Untitled source"}</h3>
              <p className="muted">{presentation.description}</p>
              <p className="source-url">{source.url}</p>
              <p>{source.excerpt}</p>
              <dl className="safe-record">
                <div><dt>Evidence ID</dt><dd>{source.evidence_id}</dd></div>
                <div><dt>Test query</dt><dd>{source.query_id || "Unknown"}</dd></div>
                <div><dt>Grounding query</dt><dd>{source.grounding_query || "Unknown"}</dd></div>
                <div><dt>Provenance</dt><dd>{source.provenance}</dd></div>
                <div><dt>Returned position</dt><dd>{source.returned_position ?? "Unknown"}</dd></div>
                <div><dt>Crawled</dt><dd>{source.crawled_at || "Unknown"}</dd></div>
                <div><dt>Last updated</dt><dd>{source.last_updated_at || "Unknown"}</dd></div>
              </dl>
              <p className="small muted">This passage is untrusted evidence. Its presence does not prove that an LLM Provider Survey response cited it correctly or that its claims are factually correct. Position applies only to the saved packet.</p>
            </article>
          )}
        </div>
      </aside>
    </>
  );
}
