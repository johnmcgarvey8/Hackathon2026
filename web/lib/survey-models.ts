import type { EvaluatedAnswer, MeasurementInputs } from "./types";

const friendlyProfileNames: Record<string, string> = {
  "chatgpt-style": "ChatGPT",
  "claude-backed": "Claude",
  "copilot-style": "Copilot",
};

export interface SurveyModelIdentity {
  profileId: string;
  friendlyName: string;
  provider: string;
  deployment: string;
  observedModel: string | null;
  label: string;
}

export function surveyModelRoster(
  inputs: MeasurementInputs | null | undefined,
  answers: EvaluatedAnswer[],
): SurveyModelIdentity[] {
  return (inputs?.profiles || []).map((profile) => {
    const observed = answers.find(
      (answer) => answer.profile_id === profile.profile_id && answer.model,
    )?.model || null;
    const friendlyName = friendlyProfileNames[profile.profile_id] || profile.profile_id;
    const exactModel = observed || profile.deployment || "Model unavailable";
    return {
      profileId: profile.profile_id,
      friendlyName,
      provider: profile.provider || "Provider unavailable",
      deployment: profile.deployment || "Deployment unavailable",
      observedModel: observed,
      label: `${friendlyName} · ${exactModel}`,
    };
  });
}
