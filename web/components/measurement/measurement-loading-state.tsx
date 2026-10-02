export type MeasurementLoadingStage =
  | "progress"
  | "queries"
  | "evidence"
  | "answers"
  | "scores"
  | "recommendations";

const loadingCopy: Record<MeasurementLoadingStage, { title: string; detail: string }> = {
  progress: {
    title: "Preparing operation progress",
    detail: "The worker is starting and saved progress will appear here shortly.",
  },
  queries: {
    title: "Generating grounding queries",
    detail: "The measured page is being analysed and evidence-backed queries are being prepared.",
  },
  evidence: {
    title: "Retrieving grounding evidence",
    detail: "WebIQ searches are queued or running. Saved citations will appear as each query resolves.",
  },
  answers: {
    title: "Surveying configured LLM providers",
    detail: "Provider responses are queued or running against the saved grounding evidence.",
  },
  scores: {
    title: "Calculating citation performance",
    detail: "Scores will appear after the provider responses and citation checks are saved.",
  },
  recommendations: {
    title: "Preparing evidence-backed recommendations",
    detail: "The recommendation stage is queued or running against the completed measurement.",
  },
};

export function MeasurementLoadingState({
  stage,
  detail,
  compact = false,
}: {
  stage: MeasurementLoadingStage;
  detail?: string;
  compact?: boolean;
}) {
  const copy = loadingCopy[stage];

  return (
    <div
      className={`measurement-pending-state ${compact ? "compact" : ""}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="measurement-loading-ring" aria-hidden="true" />
      <div className="measurement-pending-copy">
        <strong>{copy.title}</strong>
        <span>{detail || copy.detail}</span>
      </div>
      <div className="measurement-skeleton" aria-hidden="true">
        <span />
        <span />
        {!compact && <span />}
      </div>
    </div>
  );
}
