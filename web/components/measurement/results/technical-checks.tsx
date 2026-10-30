import type {
  RecommendationPlanReport,
  TechnicalField,
  TechnicalSignalName,
  TechnicalSignals,
} from "@/lib/types";

const fields: { key: TechnicalSignalName; label: string }[] = [
  { key: "jsonld_types", label: "JSON-LD types" },
  { key: "meta_title", label: "Page title" },
  { key: "meta_description", label: "Meta description" },
  { key: "canonical", label: "Canonical URL" },
  { key: "robots", label: "Robots metadata" },
  { key: "hreflang", label: "Hreflang" },
  { key: "open_graph", label: "Open Graph metadata" },
  { key: "h1", label: "H1 count" },
  { key: "h2", label: "H2 count" },
  { key: "image_alt_coverage", label: "Image alt coverage" },
  { key: "internal_link_count", label: "Internal link count" },
];

const captureReasons: Record<string, string> = {
  "feature-disabled": "HTML capture was disabled by the execution policy used for this saved report. Ask the operator to configure an approved HTML-enabled policy and matching budget grant, then explicitly regenerate recommendations.",
  "provider-unavailable": "The technical HTML provider was unavailable. The operator must configure the provider before an explicitly authorised regeneration.",
  "browse-failed": "The technical HTML request failed. Its operation is not automatically retried; an operator must check the failure before an explicitly authorised regeneration.",
  "html-not-provided": "The provider returned no usable HTML. Text-only evidence cannot establish whether page metadata or structured data is missing.",
};

function fieldText(field: TechnicalField | undefined, key: TechnicalSignalName): string {
  if (!field || field.state === "not-provided") return "Not provided";
  if (field.state === "not-found") return "Not found in captured indexed HTML";
  if (field.value === null || field.value === undefined) return "Present (value unavailable)";
  if (key === "image_alt_coverage" && typeof field.value === "number") {
    return `${Math.round(field.value * 100)}%`;
  }
  if (Array.isArray(field.value)) return field.value.join(", ");
  if (typeof field.value === "boolean") return field.value ? "Yes" : "No";
  return String(field.value);
}

export function TechnicalChecksView({
  signals,
  findings,
}: {
  signals: TechnicalSignals;
  findings: RecommendationPlanReport["technical_findings"]["findings"];
}) {
  const captured = signals.status === "captured";
  const partial = captured && signals.reason === "partial-html";
  const statusLabel = captured ? (partial ? "Partially captured" : "Captured") : "Not captured";
  const unknownCount = fields.filter(({ key }) => {
    const field = signals[key];
    return !field || field.state === "not-provided";
  }).length;
  return (
    <details className="measurement-query-group">
      <summary>
        <strong>Technical checks</strong>
        <span className="pill">{statusLabel}</span>
      </summary>
      <div className="measurement-query-content measurement-query-section technical-checks-content">
        {!captured && (
          <p className="muted">
            Technical HTML was not inspected for this saved report.{" "}
            {captureReasons[signals.reason || ""] || (
              signals.reason
                ? `Capture was unavailable: ${signals.reason}.`
                : "No capture reason was provided."
            )}
          </p>
        )}
        {captured && (
          <>
            <h4>Captured observations</h4>
            <p className="small muted">
              Coverage: {fields.length - unknownCount} of {fields.length} attributes assessable; {unknownCount} unknown.
            </p>
            <div className="table-wrap" role="region" aria-label="Captured technical observations" tabIndex={0}>
              <table className="technical-observations">
                <caption className="sr-only">Captured technical observations</caption>
                <thead>
                  <tr>
                    <th scope="col">Attribute</th>
                    <th scope="col">Status</th>
                    <th scope="col">Captured value</th>
                  </tr>
                </thead>
                <tbody>
                  {fields.map(({ key, label }) => {
                    const field = signals[key];
                    const status = !field || field.state === "not-provided"
                      ? "Unknown"
                      : field.state === "not-found" ? "Not found" : "Observed";
                    return (
                      <tr key={key}>
                        <th scope="row">{label}</th>
                        <td><span className="pill">{status}</span></td>
                        <td>{fieldText(field, key)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <h4>Deterministic findings</h4>
            {findings.length === 0 ? (
              <p className="muted">
                No deterministic technical findings were recorded. This does not establish that all technical checks passed.
              </p>
            ) : findings.map((finding) => (
              <p key={finding.check_id}>
                <strong>{finding.check_id}</strong> · {finding.field} · {finding.severity} · {finding.state}
                {finding.observed_value && <> · {finding.observed_value}</>}
              </p>
            ))}
          </>
        )}
        <p className="small muted">
          The Azure Recommendations agent uses the supplied evidence and its system instructions to decide which changes to recommend.
          Capturing HTML does not guarantee a FAQ or other structured-data recommendation.
        </p>
        {captured && (
          <>
            <p className="small muted">Crawl date: {signals.crawled_at || "Not provided"}</p>
            {signals.browse_trace_id && (
              <p className="small muted">Provider trace: <code>{signals.browse_trace_id}</code></p>
            )}
          </>
        )}
      </div>
    </details>
  );
}
