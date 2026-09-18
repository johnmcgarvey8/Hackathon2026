"use client";

import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";
import { Icon } from "@/components/icons";

export default function CmsUpdatesPage() {
  const { project } = useProject();
  if (!project) return null;
  return (
    <section className="screen">
      <ScreenHeader eyebrow="Future capability" title="CMS Updates" description="Review and publication workflows are planned for a later phase." />
      <section className="future-state card">
        <span className="future-symbol"><Icon name="cms" /></span>
        <p className="eyebrow">Disabled</p>
        <h2>CMS updates are not available</h2>
        <p>This frontend does not create, approve, or publish content. The FastAPI project API currently exposes no CMS update endpoint for {project.name}.</p>
        <button className="button primary" disabled>Prepare CMS update</button>
      </section>
    </section>
  );
}
