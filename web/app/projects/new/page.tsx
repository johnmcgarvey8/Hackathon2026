"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { MicrosoftMark } from "@/components/microsoft-mark";
import { ScreenHeader } from "@/components/screen-header";

export default function NewProjectPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [primaryDomain, setPrimaryDomain] = useState("");
  const [additionalDomains, setAdditionalDomains] = useState("");
  const [competitorDomains, setCompetitorDomains] = useState("");
  const [locale, setLocale] = useState("en-GB");
  const [activeGoal, setActiveGoal] = useState("");
  const [colour, setColour] = useState("#0067b8");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const project = await api.createProject({
        name: name.trim(),
        primary_domain: primaryDomain.trim(),
        additional_domains: additionalDomains
          .split(/[\n,]/)
          .map((domain) => domain.trim())
          .filter(Boolean),
        competitor_domains: competitorDomains
          .split(/[\n,]/)
          .map((domain) => domain.trim())
          .filter(Boolean),
        default_locale: locale,
        active_goal: activeGoal.trim() || null,
        colour,
      });
      router.push(`/projects/${project.project_id}`);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The project could not be created.",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="appbar">
        <Link className="brand-lockup" href="/projects" aria-label="Microsoft GEO Optimizer projects">
          <MicrosoftMark />
          <span className="brand-name">Microsoft</span>
          <span className="brand-divider" aria-hidden="true" />
          <span className="product-name">GEO Optimizer</span>
        </Link>
        <div className="appbar-spacer" />
        <span className="concept-badge">Local preview</span>
      </header>
      <main id="main-content" className="projects-main" tabIndex={-1}>
        <section className="screen">
          <ScreenHeader
            eyebrow="Brand workspace"
            title="Create project"
            description="Create one governed project per brand. Domains, runs and conversations remain isolated within this boundary."
            actions={<Link className="button" href="/projects">Cancel</Link>}
          />
          <form className="card project-form" onSubmit={submit}>
            <div className="form-grid">
              <label className="form-field">
                <span>Project name</span>
                <input required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} placeholder="Contoso" />
              </label>
              <label className="form-field">
                <span>Primary domain</span>
                <input required value={primaryDomain} onChange={(event) => setPrimaryDomain(event.target.value)} placeholder="contoso.com" />
                <small>Use a hostname without a path or port.</small>
              </label>
              <label className="form-field full">
                <span>Additional approved domains</span>
                <textarea value={additionalDomains} onChange={(event) => setAdditionalDomains(event.target.value)} placeholder={"www.contoso.com\ncontoso.co.uk"} />
                <small>Optional. Separate hostnames with commas or new lines.</small>
              </label>
              <label className="form-field full">
                <span>Competitor domains</span>
                <textarea value={competitorDomains} onChange={(event) => setCompetitorDomains(event.target.value)} placeholder={"competitor.com\nanother-competitor.co.uk"} />
                <small>Optional. Separate hostnames with commas or new lines. Competitor appearances will be snapshotted into each new measurement run.</small>
              </label>
              <label className="form-field">
                <span>Default locale</span>
                <select value={locale} onChange={(event) => setLocale(event.target.value)}>
                  <option value="en-GB">English (United Kingdom)</option>
                  <option value="en-US">English (United States)</option>
                  <option value="fr-FR">French (France)</option>
                  <option value="de-DE">German (Germany)</option>
                </select>
              </label>
              <label className="form-field">
                <span>Project colour</span>
                <input type="color" value={colour} onChange={(event) => setColour(event.target.value)} />
              </label>
              <label className="form-field full">
                <span>Active goal</span>
                <textarea maxLength={500} value={activeGoal} onChange={(event) => setActiveGoal(event.target.value)} placeholder="Improve GEO visibility for priority product pages." />
              </label>
            </div>
            {error && <p className="form-error" role="alert">{error}</p>}
            <div className="button-row">
              <button className="button primary" type="submit" disabled={saving}>{saving ? "Creating..." : "Create project"}</button>
              <Link className="button" href="/projects">Cancel</Link>
            </div>
          </form>
        </section>
      </main>
    </>
  );
}
