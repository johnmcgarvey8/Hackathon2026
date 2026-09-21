"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { agentLabel, runtimeLabel } from "@/lib/chat-runtime";
import type { ChatStatus } from "@/lib/types";
import { useProject } from "@/components/project-context";
import { ScreenHeader } from "@/components/screen-header";
import { UnavailableState } from "@/components/status-state";

const grounding = [
  { name: "WebIQ", asset: "/assets/webiq.svg", text: "Public discovery and citation evidence" },
  { name: "FoundryIQ", asset: "/assets/foundry.svg", text: "Model reasoning and evaluation grounding" },
  { name: "FabricIQ", asset: "/assets/fabric.svg", text: "Governed enterprise data grounding" },
];

export default function IntegrationsPage() {
  const { project } = useProject();
  const [runtime, setRuntime] = useState<ChatStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!project) return;
    let active = true;
    api.chatStatus(project.project_id)
      .then((status) => { if (active) setRuntime(status); })
      .catch((requestError) => {
        if (active) setError(requestError instanceof Error ? requestError.message : "Runtime status could not be loaded.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [project]);

  if (!project) return null;

  return (
    <section className="screen">
      <ScreenHeader eyebrow="Project configuration" title="Integrations" description="Review grounding and project-scoped platform connections." />
      <div className="integration-categories">
        <details className="integration-category" open>
          <summary><span className="category-symbol iq">IQ</span><span className="category-heading"><strong>Grounding services</strong><small>Shared evidence and reasoning services</small></span><span className="pill blue">Platform</span></summary>
          <div className="integration-category-content">
            <ul className="grounding-list">
              {grounding.map((item) => <li key={item.name}><Image className="connector-logo" src={item.asset} width={36} height={36} alt="" /><span className="grounding-copy"><strong>{item.name}</strong><small>{item.text}</small></span><span className="pill amber">Not connected to project chat</span></li>)}
            </ul>
          </div>
        </details>
        <details className="integration-category" open>
          <summary><span className="category-symbol analytics">A</span><span className="category-heading"><strong>Analytics</strong><small>Project measurement signals</small></span></summary>
          <div className="integration-category-content">
            <UnavailableState title="Analytics connection unavailable" message="The current FastAPI project API does not expose an integrations endpoint. No analytics connection status can be verified." compact />
          </div>
        </details>
        <details className="integration-category" open>
          <summary><span className="category-symbol cms">CMS</span><span className="category-heading"><strong>Content management</strong><small>Governed content destinations</small></span></summary>
          <div className="integration-category-content">
            <UnavailableState title="CMS connection unavailable" message="The current FastAPI project API does not expose CMS connector data. Publishing controls are intentionally unavailable." compact />
          </div>
        </details>
        <details className="integration-category" open>
          <summary><span className="category-symbol iq">AI</span><span className="category-heading"><strong>Project assistant</strong><small>Microsoft Foundry runtime</small></span><span className={`pill ${runtime?.can_send ? "amber" : "red"}`}>{loading ? "Loading runtime" : runtimeLabel(runtime)}</span></summary>
          <div className="integration-category-content">
            {agentLabel(runtime) && <p><strong>{agentLabel(runtime)}</strong></p>}
            {error && <UnavailableState title="Runtime status unavailable" message={error} compact />}
            <p className="muted">Status is provided by the project BFF. Credentials and bearer tokens remain server-only.</p>
          </div>
        </details>
      </div>
    </section>
  );
}
