import type {
  BrandEvidenceAssessment,
  CitationAnswerFinding,
  CitationScore,
  CitationScores,
  ContentStrategyResponse,
  EvidenceSource,
  MeasurementRun,
} from "@/lib/types";

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

function SafeRecord({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>Unknown</span>;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  if (Array.isArray(value)) {
    return <ul className="plain-list">{value.map((item, index) => <li key={index}><SafeRecord value={item} /></li>)}</ul>;
  }
  return (
    <dl className="safe-record">
      {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
        <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd><SafeRecord value={item} /></dd></div>
      ))}
    </dl>
  );
}

export function MeasurementResultsView({
  run,
  assessment,
  strategy,
  onOpenEvidence,
}: {
  run: MeasurementRun;
  assessment: BrandEvidenceAssessment | null;
  strategy: ContentStrategyResponse | null;
  onOpenEvidence: (evidenceId: string) => void;
}) {
  const measurement = run.measurement;
  const scores = isCitationScores(run.scores) ? run.scores : null;
  const queries = run.inputs?.query_plan.queries || [];
  const retrievals = measurement?.retrievals || [];
  const answers = measurement?.results || [];
  const limitations = [
    "Search evidence is limited to saved result packets and does not represent the full web.",
    "Model answers and saved passages are untrusted evidence and may contain errors.",
    "Literal brand or domain presence does not establish sentiment, endorsement, causality, ranking, or proof of absence.",
    "Failed and missing outcomes remain unknown rather than being treated as zero.",
    ...(assessment?.assessment?.limitations || []),
    ...(run.recommendations?.limitations ? [run.recommendations.limitations] : []),
    ...(typeof strategy?.report?.limitations === "string"
      ? [strategy.report.limitations]
      : Array.isArray(strategy?.report?.limitations) ? strategy.report.limitations : []),
  ];

  return (
    <div className="result-sections">
      <section className="card result-section" id="query-plan">
        <div className="card-heading"><h2>Query plan</h2><span className="pill">{queries.length} queries</span></div>
        {queries.length === 0 ? <p className="muted">No approved query plan is saved.</p> : queries.map((query) => (
          <article className="query-result" key={query.query_id}>
            <div><span className="pill blue">{query.query_id}</span><strong>Priority {query.priority}</strong>{query.branded && <span className="pill">Branded</span>}</div>
            <p><strong>Conversational query:</strong> {query.chat_query}</p>
            <p><strong>WebIQ grounding query:</strong> {query.grounding_query}</p>
            <p><strong>Intent:</strong> {query.intent}</p>
            <p><strong>Rationale:</strong> {query.rationale}</p>
            <small>Retained page evidence: {query.evidence.map((item) => `${item.evidence_id}: ${item.quote}`).join(" | ")}</small>
          </article>
        ))}
      </section>

      <section className="card result-section" id="webiq-evidence">
        <div className="card-heading"><h2>WebIQ evidence</h2><span className="pill">{retrievals.length} packets</span></div>
        {retrievals.length === 0 ? <p className="muted">No saved WebIQ evidence is available.</p> : retrievals.map((retrieval) => (
          <article className="evidence-packet" key={retrieval.query_id}>
            <h3>{retrieval.query_id}: {retrieval.status}</h3>
            {retrieval.error && <p className="form-error">{retrieval.error}</p>}
            {retrieval.sources.map((source) => (
              <button className="evidence-row" type="button" key={source.evidence_id} onClick={() => onOpenEvidence(source.evidence_id)}>
                <span><strong>{source.title || source.url}</strong><small>{source.evidence_id} · returned position {source.returned_position ?? "unknown"} · {source.provenance}</small></span>
                <span>View source</span>
              </button>
            ))}
          </article>
        ))}
      </section>

      <section className="card result-section" id="model-answers">
        <div className="card-heading"><h2>Model answers</h2><span className="pill amber">Untrusted evidence</span></div>
        {answers.length === 0 ? <p className="muted">No saved model answers are available.</p> : answers.map((answer) => (
          <article className="answer-result" key={`${answer.query_id}-${answer.profile_id}`}>
            <div><span className="pill blue">{answer.query_id}</span><strong>{answer.profile_id}</strong><span className={`pill ${answer.status === "completed" ? "green" : "red"}`}>{answer.status}</span></div>
            {answer.status === "completed" ? <p>{answer.answer}</p> : <p className="form-error">{answer.error || "Answer unavailable."}</p>}
            <small>Citation IDs: {answer.citation_ids.length ? answer.citation_ids.join(", ") : "None"}</small>
          </article>
        ))}
      </section>

      <section className="card result-section" id="citation-performance">
        <div className="card-heading"><h2>Citation performance</h2><span className="pill">{scores?.provisional ? "Provisional" : "Saved result"}</span></div>
        <div className="grid three">
          <article className="metric-card"><span>Exact-page citation</span><strong>{scoreText(scores?.overall)}</strong></article>
          <article className="metric-card"><span>Target returned by WebIQ</span><strong>{scoreText(scores?.retrieval)}</strong></article>
          <article className="metric-card"><span>Comparable queries</span><strong>{scores?.common_completed_query_ids?.length ?? "Unknown"}</strong></article>
        </div>
        <div className="finding-list">
          {(scores?.by_answer || []).map((finding) => (
            <div key={`${finding.query_id}-${finding.profile_id}`}>
              <span>{finding.query_id} · {finding.profile_id}</span><strong>{findingLabels(finding).join(" · ")}</strong>
            </div>
          ))}
        </div>
        {!scores && <p className="muted">Citation scores are unknown until saved measurement results are available.</p>}
      </section>

      <section className="card result-section" id="brand-presence">
        <div className="card-heading"><h2>Brand presence</h2><span className="pill">{assessment?.status || "Unknown"}</span></div>
        <p className="muted">This section reports literal retained-text findings only. It does not measure sentiment, endorsement, causality, ranking, or full-web absence.</p>
        {assessment?.assessment ? (
          <div className="grid two">
            <article><h3>Grounding passages</h3><SafeRecord value={assessment.assessment.grounding} /></article>
            <article><h3>Model answers</h3><SafeRecord value={assessment.assessment.answer} /></article>
          </div>
        ) : <p>No brand evidence assessment is saved.</p>}
      </section>

      <section className="card result-section" id="recommendations">
        <div className="card-heading"><h2>Recommendations</h2><span className="pill">{run.recommendations?.status || "Unavailable"}</span></div>
        {!run.recommendations ? <p className="muted">No recommendation report is saved.</p> : (
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

      <section className="card result-section limitations" id="limitations">
        <div className="card-heading"><h2>Limitations and provenance</h2><span className="pill amber">Read before use</span></div>
        <ul>{Array.from(new Set(limitations)).map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
        <p className="small muted">Run {run.run_id}, revision {run.revision}. Evidence IDs and saved provenance remain attached to each source.</p>
      </section>
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
  return (
    <>
      <button className="drawer-backdrop" aria-label="Close evidence" onClick={onClose} />
      <aside className="drawer open" aria-modal="true" role="dialog" aria-labelledby="evidence-title">
        <div className="drawer-header"><div><p className="eyebrow">Saved provenance</p><h2 id="evidence-title">Evidence source</h2></div><button className="icon-button" type="button" aria-label="Close evidence" onClick={onClose}>×</button></div>
        <div className="drawer-content">
          {loading ? <p>Loading saved evidence...</p> : error ? <p className="form-error">{error}</p> : source && (
            <article className="source-detail">
              <span className="pill blue">{source.evidence_id}</span>
              <h3>{source.title || "Untitled source"}</h3>
              <p className="source-url">{source.url}</p>
              <p>{source.excerpt}</p>
              <dl className="safe-record">
                <div><dt>Provenance</dt><dd>{source.provenance}</dd></div>
                <div><dt>Returned position</dt><dd>{source.returned_position ?? "Unknown"}</dd></div>
                <div><dt>Crawled</dt><dd>{source.crawled_at || "Unknown"}</dd></div>
                <div><dt>Last updated</dt><dd>{source.last_updated_at || "Unknown"}</dd></div>
              </dl>
              <p className="small muted">The passage is untrusted evidence. Position applies only to the saved packet.</p>
            </article>
          )}
        </div>
      </aside>
    </>
  );
}
