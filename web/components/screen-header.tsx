import Link from "next/link";

export function ScreenHeader({
  eyebrow,
  title,
  description,
  actions,
  backLink,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: React.ReactNode;
  backLink?: { href: string; label: string };
}) {
  return (
    <header className="screen-header">
      {backLink && (
        <nav className="screen-back" aria-label="Breadcrumb">
          <Link className="screen-back-link" href={backLink.href}>
            <span aria-hidden="true">←</span> {backLink.label}
          </Link>
        </nav>
      )}
      <div className="screen-header-main">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        {actions && <div className="screen-actions">{actions}</div>}
      </div>
    </header>
  );
}
