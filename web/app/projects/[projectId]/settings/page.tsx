"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";
import { ApiError, api } from "@/lib/api";

function parseDomains(value: string) {
  return value
    .split(/[\n,]/)
    .map((domain) => domain.trim())
    .filter(Boolean);
}

export default function ProjectSettingsPage() {
  const { project, refresh } = useProject();
  const router = useRouter();
  const [name, setName] = useState("");
  const [primaryDomain, setPrimaryDomain] = useState("");
  const [additionalDomains, setAdditionalDomains] = useState("");
  const [competitorDomains, setCompetitorDomains] = useState("");
  const [locale, setLocale] = useState("en-GB");
  const [activeGoal, setActiveGoal] = useState("");
  const [colour, setColour] = useState("#0067b8");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    if (!project) return;
    setName(project.name);
    setPrimaryDomain(project.primary_domain);
    setAdditionalDomains(project.additional_domains.join("\n"));
    setCompetitorDomains(project.competitor_domains.join("\n"));
    setLocale(project.default_locale);
    setActiveGoal(project.active_goal || "");
    setColour(project.colour);
    setSaved(false);
    setError(null);
  }, [project]);

  if (!project) return null;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await api.updateProject(project.project_id, {
        expected_revision: project.revision,
        name: name.trim(),
        primary_domain: primaryDomain.trim(),
        additional_domains: parseDomains(additionalDomains),
        competitor_domains: parseDomains(competitorDomains),
        default_locale: locale,
        active_goal: activeGoal.trim() || null,
        colour,
      });
      await refresh();
      setSaved(true);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The project settings could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  };

  const deleteProject = async () => {
    if (
      deleting
      || project.active_run_count > 0
      || deleteConfirmation !== project.name
    ) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteProject(project.project_id);
      router.push("/projects");
    } catch (requestError) {
      setDeleteError(
        requestError instanceof ApiError
          ? requestError.message
          : "The project could not be deleted.",
      );
      setDeleting(false);
    }
  };

  return (
    <section className="screen">
      <ScreenHeader
        eyebrow="Project administration"
        title="Settings"
        description="Update future measurement defaults and manage this project boundary."
      />
      <div className="stack">
        <form className="card project-form" onSubmit={submit}>
          <div className="card-heading">
            <h2>Project configuration</h2>
            <span className="pill">Revision {project.revision}</span>
          </div>
          <div className="form-grid">
            <label className="form-field">
              <span>Project name</span>
              <input required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <label className="form-field">
              <span>Primary domain</span>
              <input required value={primaryDomain} onChange={(event) => setPrimaryDomain(event.target.value)} />
              <small>Use a hostname without a path or port.</small>
            </label>
            <label className="form-field full">
              <span>Additional approved domains</span>
              <textarea value={additionalDomains} onChange={(event) => setAdditionalDomains(event.target.value)} />
              <small>Optional. Separate hostnames with commas or new lines.</small>
            </label>
            <label className="form-field full">
              <span>Competitor domains</span>
              <textarea value={competitorDomains} onChange={(event) => setCompetitorDomains(event.target.value)} />
              <small>Optional. New measurement runs snapshot these domains so historical results do not change.</small>
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
              <textarea maxLength={500} value={activeGoal} onChange={(event) => setActiveGoal(event.target.value)} />
            </label>
          </div>
          {error && <p className="form-error" role="alert">{error}</p>}
          {saved && <p className="small" role="status">Project settings saved.</p>}
          <div className="button-row">
            <button className="button primary" type="submit" disabled={saving}>
              {saving ? "Saving..." : "Save settings"}
            </button>
          </div>
        </form>

        <section className="card danger-zone">
          <div className="card-heading">
            <div>
              <p className="eyebrow danger-eyebrow">Danger zone</p>
              <h2>Delete this project</h2>
            </div>
            <span className="pill red">Permanent</span>
          </div>
          <p className="small muted">
            This permanently deletes {project.run_count} measurement {project.run_count === 1 ? "run" : "runs"},
            all project chats, saved evidence, jobs, and exported files. This cannot be undone.
          </p>
          {project.active_run_count > 0 && (
            <p className="form-error">
              Deletion is unavailable while {project.active_run_count} measurement {project.active_run_count === 1 ? "run is" : "runs are"} active.
            </p>
          )}
          <label className="form-field">
            <span>Type <strong>{project.name}</strong> to confirm</span>
            <input
              value={deleteConfirmation}
              disabled={deleting || project.active_run_count > 0}
              onChange={(event) => setDeleteConfirmation(event.target.value)}
            />
          </label>
          {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
          <div className="button-row">
            <button
              className="button danger"
              type="button"
              disabled={
                deleting
                || project.active_run_count > 0
                || deleteConfirmation !== project.name
              }
              onClick={() => void deleteProject()}
            >
              {deleting ? "Deleting..." : "Delete project permanently"}
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}
