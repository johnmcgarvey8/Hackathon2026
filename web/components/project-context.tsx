"use client";

import { createContext, useContext } from "react";
import type { Project } from "@/lib/types";

interface ProjectContextValue {
  project: Project | null;
  projects: Project[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export const ProjectContext = createContext<ProjectContextValue | null>(null);

export function useProject() {
  const context = useContext(ProjectContext);
  if (!context) throw new Error("useProject must be used inside ProjectShell");
  return context;
}
