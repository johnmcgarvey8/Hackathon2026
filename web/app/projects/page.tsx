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
                    <Link className="button" href={`/projects/${project.project_id}/settings`}>Settings</Link>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
    </>
  );
}
