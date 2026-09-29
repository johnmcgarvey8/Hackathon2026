"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { Project } from "@/lib/types";
import { MicrosoftMark } from "@/components/microsoft-mark";
import { ScreenHeader } from "@/components/screen-header";
import { LoadingState, UnavailableState } from "@/components/status-state";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setProjects((await api.projects()).filter((project) => !project.archived));
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Projects could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!deleteTarget) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !deleting) setDeleteTarget(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [deleteTarget, deleting]);

  const openDelete = (project: Project) => {
    setDeleteTarget(project);
    setDeleteConfirmation("");
    setDeleteError(null);
  };

  const deleteProject = async () => {
    if (!deleteTarget || deleteConfirmation !== deleteTarget.name || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteProject(deleteTarget.project_id);
      setProjects((items) => items.filter((item) => item.project_id !== deleteTarget.project_id));
      setDeleteTarget(null);
      setDeleteConfirmation("");
    } catch (requestError) {
      setDeleteError(
        requestError instanceof ApiError ? requestError.message : "The project could not be deleted.",
      );
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="appbar">
        <div className="brand-lockup" aria-label="Microsoft GEO Optimizer">
          <MicrosoftMark />
          <span className="brand-name">Microsoft</span>
          <span className="brand-divider" aria-hidden="true" />
          <span className="product-name">GEO Optimizer</span>
        </div>
        <div className="appbar-spacer" />
        <span className="concept-badge">Local preview</span>
        <div className="persona" aria-label="Signed in operator">
          <span className="avatar">AM</span>
          <span className="persona-copy"><strong>Alex Morgan</strong><small>GEO manager</small></span>
        </div>
      </header>
      <main id="main-content" className="projects-main" tabIndex={-1}>
        <section className="screen">
          <ScreenHeader
            eyebrow="Your workspace"
            title="Projects"
            description="Choose a project to open its GEO workspace."
            actions={<Link className="button primary" href="/projects/new">Create project</Link>}
          />
          {loading ? <LoadingState label="Loading projects" /> : error ? (
            <div className="stack">
              <UnavailableState message={error} />
              <button className="button retry-button" onClick={() => void load()}>Try again</button>
            </div>
          ) : projects.length === 0 ? (
            <div className="stack">
              <UnavailableState title="No projects available" message="Create a brand project to start a governed GEO workspace." />
              <Link className="button primary retry-button" href="/projects/new">Create project</Link>
            </div>
          ) : (
            <div className="grid two">
              {projects.map((project) => (
                <article className="card project-card" key={project.project_id}>
                  <div className="project-title">
                    <span className="project-avatar" style={{ background: project.colour }}>{project.initials}</span>
                    <div>
                      <h2>{project.name}</h2>
                      <small>{project.primary_domain}</small>
                    </div>
                  </div>
                  <div className="project-meta">
                    <div className="meta-block"><span>Active goal</span><strong>{project.active_goal || "No goal set"}</strong></div>
                    <div className="meta-block"><span>Runs</span><strong>{project.run_count}</strong></div>
                  </div>
                  <div className="project-actions">
                    <Link className="button primary" href={`/projects/${project.project_id}`}>Open project</Link>
                    <button
                      className="button danger"
                      type="button"
                      disabled={project.active_run_count > 0}
                      title={project.active_run_count > 0 ? "Active measurement runs must finish or be cancelled first." : undefined}
                      onClick={() => openDelete(project)}
                    >
                      Delete project
                    </button>
                  </div>
                  {project.active_run_count > 0 && (
                    <p className="small muted">Deletion is unavailable while {project.active_run_count} measurement {project.active_run_count === 1 ? "run is" : "runs are"} active.</p>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
      {deleteTarget && (
        <>
          <button
            className="dialog-backdrop"
            type="button"
            aria-label="Cancel project deletion"
            disabled={deleting}
            onClick={() => setDeleteTarget(null)}
          />
          <section
            className="confirmation-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-project-title"
          >
            <p className="eyebrow">Permanent deletion</p>
            <h2 id="delete-project-title">Delete {deleteTarget.name}?</h2>
            <p>
              This permanently deletes {deleteTarget.run_count} measurement {deleteTarget.run_count === 1 ? "run" : "runs"},
              all project chats, saved evidence, jobs, and exported files.
            </p>
            <label className="form-field">
              <span>Type <strong>{deleteTarget.name}</strong> to confirm</span>
              <input
                autoFocus
                value={deleteConfirmation}
                disabled={deleting}
                onChange={(event) => setDeleteConfirmation(event.target.value)}
              />
            </label>
            {deleteError && <p className="form-error">{deleteError}</p>}
            <div className="button-row">
              <button className="button" type="button" disabled={deleting} onClick={() => setDeleteTarget(null)}>Cancel</button>
              <button
                className="button danger"
                type="button"
                disabled={deleting || deleteConfirmation !== deleteTarget.name}
                onClick={() => void deleteProject()}
              >
                {deleting ? "Deleting..." : "Delete permanently"}
              </button>
            </div>
          </section>
        </>
      )}
    </>
  );
}
