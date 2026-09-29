import type { ChatStatus, MeasurementWorkflow } from "./types";

const pollableWorkflowStatuses = new Set(["preparing", "evaluating"]);

export function runtimeLabel(runtime: ChatStatus | null): string {
  if (!runtime) return "Runtime status unavailable";
  if (runtime.mode === "unavailable") return "Live agent unavailable";
  return "Live Foundry selected";
}

export function agentLabel(runtime: ChatStatus | null): string | null {
  if (!runtime?.agent) return null;
  const { name, version, scope } = runtime.agent;
  return `${name} · version ${version} · ${scope === "shared-default" ? "Shared default" : "Project override"}`;
}

export function shouldPollMeasurementWorkflow(
  workflow: MeasurementWorkflow | null | undefined,
): boolean {
  return Boolean(workflow && pollableWorkflowStatuses.has(workflow.status));
}

export function shouldPollMeasurementWorkflows(
  workflows: MeasurementWorkflow[] | null | undefined,
): boolean {
  return Boolean(workflows?.some(shouldPollMeasurementWorkflow));
}
