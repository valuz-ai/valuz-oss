/**
 * Client for /v1/onboarding/* — the one-shot endpoint that wires up the
 * example project at the end of the first-run flow. Creates (or reuses)
 * ~/Valuz/示例项目, binds it as a project, and deploys the chosen team's
 * official agents into it. Fully idempotent server-side.
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setOnboardingApiBase = (url: string): void => {
  _apiBase = url;
};

/** The three preset teams that map to real seeded agent rosters. */
export type OnboardingTeamId = "general" | "investment" | "product";

export interface ExampleProjectResponse {
  project_id: string;
  project_name: string;
}

export interface AssistantResponse {
  agent_slug: string;
}

const fetchJson = createFetchJson(() => _apiBase);

export const onboardingApi = {
  /** Create (or reuse) the example project and deploy the team's agents. */
  createExampleProject(
    teamId: OnboardingTeamId,
  ): Promise<ExampleProjectResponse> {
    return fetchJson<ExampleProjectResponse>("/v1/onboarding/example-project", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id: teamId }),
    });
  },

  /** Create (or reuse) only the general Valuz 小助手 — no project. */
  createAssistant(): Promise<AssistantResponse> {
    return fetchJson<AssistantResponse>("/v1/onboarding/assistant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
  },
};
