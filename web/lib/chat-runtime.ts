import type { ChatStatus } from "./types";

export function runtimeLabel(runtime: ChatStatus | null): string {
  if (!runtime) return "Runtime status unavailable";
  if (runtime.mode === "mock") return "Mock assistant";
  if (runtime.mode === "unavailable") return "Live agent unavailable";
  return "Live Foundry selected";
}

export function agentLabel(runtime: ChatStatus | null): string | null {
  if (!runtime?.agent) return null;
  const { name, version, scope } = runtime.agent;
  return `${name} · version ${version} · ${scope === "shared-default" ? "Shared default" : "Project override"}`;
}

export function budgetLabel(runtime: ChatStatus | null): string | null {
  if (!runtime?.budget) return null;
  const { remaining, limit, used } = runtime.budget;
  return `Owner budget: ${remaining} of ${limit} requests remaining (${used} used), shared across projects`;
}
