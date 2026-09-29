export function UnavailableState({
  title = "Service unavailable",
  message,
  compact = false,
}: {
  title?: string;
  message: string;
  compact?: boolean;
}) {
  return (
    <section className={`unavailable-state ${compact ? "compact" : ""}`} role="status">
      <span className="unavailable-symbol">!</span>
      <div>
        <h2>{title}</h2>
        <p>{message}</p>
      </div>
    </section>
  );
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="loading-ring" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
