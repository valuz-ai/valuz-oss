import type { ComponentType } from "react";
import { activeProfile, type DesktopRouteModule } from "@valuz/core";
import {
  ActivityPage,
  AgentDetailPage,
  AgentsPage,
  ApiKeyConfigPage,
  AutomationPage,
  ConversationPage,
  ConnectorsPage,
  ConversationsHomePage,
  ContextPanelPage,
  OnboardingFlow,
  KnowledgePage,
  OnboardingPage,
  OverlaysPage,
  ProjectDetailPage,
  ProjectsPage,
  SettingsPage,
  SkillDetailPage,
  SkillsPage,
  TaskDetailPage,
  ToolCallsPage,
} from "../pages";

const COMPONENT_MAP: Record<string, ComponentType> = {
  "conversations-home": ConversationsHomePage,
  "conversation-detail": ConversationPage,
  projects: ProjectsPage,
  "project-detail": ProjectDetailPage,
  "context-panel": ContextPanelPage,
  knowledge: KnowledgePage,
  connectors: ConnectorsPage,
  agents: AgentsPage,
  "agent-detail": AgentDetailPage,
  "task-detail": TaskDetailPage,
  overlays: OverlaysPage,
  activity: ActivityPage,
  automation: AutomationPage,
  skills: SkillsPage,
  "skill-detail": SkillDetailPage,
  settings: SettingsPage,
  onboarding: OnboardingPage,
  // /welcome — the first-run entry. The full-screen editorial flow
  // (OnboardingFlow) owns welcome → connect (paste API key / CLI login) →
  // team. There is no hosted-account OAuth path.
  "first-launch": OnboardingFlow,
  "api-key-config": ApiKeyConfigPage,
  "tool-calls": ToolCallsPage,
};

export interface ResolvedRoute extends DesktopRouteModule {
  Component: ComponentType;
}

const resolveComponent = (route: DesktopRouteModule): ComponentType | null =>
  route.component ?? COMPONENT_MAP[route.id] ?? null;

export const resolveRoutes = (modules: DesktopRouteModule[]): ResolvedRoute[] =>
  modules.flatMap((route) => {
    const Component = resolveComponent(route);
    if (!Component) {
      if (typeof console !== "undefined") {
        console.warn(
          `[routes] No component registered for "${route.id}" (${route.path}); route skipped.`,
        );
      }
      return [];
    }
    return [{ ...route, Component }];
  });

export const resolvedDesktopRoutes: ResolvedRoute[] = resolveRoutes(
  activeProfile.desktopRoutes,
);
