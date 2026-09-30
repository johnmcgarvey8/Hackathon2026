"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { Project } from "@/lib/types";
import { Icon, type IconName } from "./icons";
import { MicrosoftMark } from "./microsoft-mark";
import { ProjectContext } from "./project-context";
import { LoadingState, UnavailableState } from "./status-state";

const navItems: { href: string; label: string; icon: IconName; disabled?: boolean }[] = [
  { href: "", label: "Dashboard", icon: "dashboard" },
  { href: "/control-plane", label: "Control Plane", icon: "control" },
  { href: "/chat", label: "Chat", icon: "chat" },
  { href: "/cms-updates", label: "CMS Updates", icon: "cms", disabled: true },
  { href: "/integrations", label: "Integrations", icon: "integrations" },
  { href: "/settings", label: "Settings", icon: "settings" },
];

export function ProjectShell({ children }: { children: React.ReactNode }) {
  const params = useParams<{ projectId: string }>();
  const pathname = usePathname();
  const router = useRouter();
  const projectId = params.projectId;
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [allProjects, currentProject] = await Promise.all([
        api.projects(),
        api.project(projectId),
      ]);
      setProjects(allProjects.filter((item) => !item.archived));
      setProject(currentProject);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The project could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => setNavOpen(false), [pathname]);

  const context = useMemo(
    () => ({ project, projects, loading, error, refresh }),
    [project, projects, loading, error, refresh],
  );

  const switchProject = (value: string) => {
    if (value === "all") {
      router.push("/projects");
      return;
    }
    const suffix = pathname.replace(`/projects/${projectId}`, "");
    const safeSuffix = ["/control-plane", "/chat", "/integrations", "/settings"].includes(suffix)
      ? suffix
      : "";
    router.push(`/projects/${value}${safeSuffix}`);
  };

  return (
    <ProjectContext.Provider value={context}>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="appbar">
        <button
          className="icon-button mobile-menu"
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={navOpen}
          onClick={() => setNavOpen((open) => !open)}
        >
          <Icon name="menu" />
        </button>
        <Link className="brand-lockup" href="/projects" aria-label="Microsoft GEO Optimizer projects">
          <MicrosoftMark />
          <span className="brand-name">Microsoft</span>
          <span className="brand-divider" aria-hidden="true" />
          <span className="product-name">GEO Optimizer</span>
        </Link>
        <label className="project-picker">
          <span>Project</span>
          <select value={projectId} onChange={(event) => switchProject(event.target.value)}>
            <option value="all">All projects</option>
            {projects.map((item) => (
              <option value={item.project_id} key={item.project_id}>{item.name}</option>
            ))}
            {!projects.some((item) => item.project_id === projectId) && (
              <option value={projectId}>{project?.name || "Current project"}</option>
            )}
          </select>
        </label>
        <div className="appbar-spacer" />
        <span className="concept-badge">Local preview</span>
        <div className="persona" aria-label="Signed in operator">
          <span className="avatar">AM</span>
          <span className="persona-copy"><strong>Alex Morgan</strong><small>GEO manager</small></span>
        </div>
      </header>
      <div className={`app-shell ${pathname.endsWith("/chat") ? "chat-workspace" : ""}`}>
        {navOpen && <button className="nav-backdrop" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
        <aside className={`navigation ${navOpen ? "open" : ""}`} aria-label="Primary navigation">
          <nav>
            {navItems.map((item) => {
              const href = `/projects/${projectId}${item.href}`;
              const active = pathname === href
                || (item.href === "/control-plane" && (
                  pathname.startsWith(`${href}/`) || pathname.includes("/measurements/")
                ));
              return (
                <Link
                  className={`nav-item ${active ? "active" : ""} ${item.disabled ? "future" : ""}`}
                  href={href}
                  key={item.label}
                  aria-current={active ? "page" : undefined}
                >
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                  {item.disabled && <span className="nav-tag">Soon</span>}
                </Link>
              );
            })}
          </nav>
        </aside>
        <main id="main-content" className={`main-content ${pathname.endsWith("/chat") ? "is-chat" : ""}`} tabIndex={-1}>
          {loading ? <LoadingState label="Loading project" /> : error ? (
            <div className="screen status-screen">
              <UnavailableState message={error} />
              <button className="button" onClick={() => void refresh()}>Try again</button>
            </div>
          ) : children}
        </main>
      </div>
    </ProjectContext.Provider>
  );
}
