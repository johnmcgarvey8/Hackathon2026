"use client";

import Link from "next/link";
import { Icon } from "@/components/icons";
import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";

function scoreFromProject(project: ReturnType<typeof useProject>["project"]) {
  if (!project?.latest_run?.scores) return null;
  const values = Object.values(project.latest_run.scores).filter((value) => Number.isFinite(value));
  if (!values.length) return null;
  const raw = values.reduce((sum, value) => sum + value, 0) / values.length;
  return raw <= 1 ? Math.round(raw * 100) : Math.round(raw);
}

export default function DashboardPage() {
  const { project } = useProject();
  if (!project) return null;
  const score = scoreFromProject(project);

  return (
    <section className="screen">
      <ScreenHeader
        eyebrow="Project dashboard"
        title={`${project.name} overview`}
        description="Track GEO goals, measurement activity, and the context available to this project."
      />
      <div className="stack dashboard-overview">
        <section className="card score-trend">
          <div className="score-trend-heading">
            <div><h2><Icon name="trend" /> GEO performance</h2><p>Latest project measurement summary</p></div>
            <span className="chart-period">Current project<span>Live BFF data</span></span>
          </div>
          <div className="score-headline">
            <div className="score-current"><span>Latest score</span><strong>{score ?? "N/A"}{score !== null && <small>/100</small>}</strong></div>
            <div className="score-growth">
              <span><Icon name="trend" /> {project.run_count} runs</span>
              <small>Project activity <b>{project.active_run_count} active</b></small>
            </div>
          </div>
          <div className="score-placeholder" aria-label={score === null ? "No score available" : `Latest GEO score ${score} out of 100`}>
            <div className="score-placeholder-grid" />
            {score === null ? <p>Complete a measurement run to populate the performance view.</p> : (
              <div className="score-bar"><span style={{ width: `${Math.min(100, Math.max(0, score))}%` }} /></div>
            )}
          </div>
        </section>

        <section className="dashboard-opportunities" aria-labelledby="project-activity-title">
          <h2 id="project-activity-title">Project activity</h2>
          <div className="grid three">
            <article className="metric-card"><span>Measurement runs</span><strong>{project.run_count}</strong><small>Bound to this project</small></article>
            <article className="metric-card"><span>Active runs</span><strong>{project.active_run_count}</strong><small>In progress now</small></article>
            <article className="metric-card"><span>Domains</span><strong>{project.domains.length}</strong><small>{project.default_locale}</small></article>
          </div>
        </section>

        <section className="card dashboard-context">
          <div className="card-heading"><h2>Project overview</h2><span className="pill green">Active</span></div>
          <div className="summary-line"><span className="status-dot success" /><span><strong>Current goal</strong><small>{project.active_goal || "No active goal has been set."}</small></span></div>
          <div className="summary-line"><span className="status-dot success" /><span><strong>Project scope</strong><small>{project.domains.join(", ")}</small></span></div>
          <div className="summary-line"><span className={`status-dot ${project.foundry_status === "configured-unverified" ? "warning" : ""}`} /><span><strong>Assistant runtime</strong><small>{project.foundry_status === "configured-unverified" ? "Foundry configured, verification pending." : "Manual Foundry setup required. Local mock chat may still be available."}</small></span></div>
          <div className="button-row">
            <Link className="button primary" href={`/projects/${project.project_id}/measurements/new`}>Create measurement</Link>
            <Link className="button primary" href={`/projects/${project.project_id}/chat`}>Open GEO assistant</Link>
            <Link className="button" href={`/projects/${project.project_id}/control-plane`}>View Control Plane</Link>
          </div>
        </section>
      </div>
    </section>
  );
}
