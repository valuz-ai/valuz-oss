import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  ConversationPage as DesktopConversationPage,
  KnowledgePage as DesktopKnowledgePage,
  OnboardingPage as DesktopOnboardingPage,
  SettingsPage as DesktopSettingsPage,
  SkillsPage as DesktopSkillsPage,
} from "@valuz/app/pages";
import { Navigate } from "react-router-dom";

describe("prototype-backed desktop pages", () => {
  it("redirects home to new conversation page", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="/"
            element={<Navigate to="/conversation/new" replace />}
          />
          <Route
            path="/conversation/:id"
            element={<DesktopConversationPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.queryByText("新对话")).toBeNull();
  });

  it("renders the conversation page empty state when no backend", () => {
    render(
      <MemoryRouter initialEntries={["/conversation/local-agent"]}>
        <Routes>
          <Route
            path="/conversation/:id"
            element={<DesktopConversationPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("新对话")).toBeTruthy();
  });

  it("renders the knowledge page hero copy", () => {
    render(<DesktopKnowledgePage />);

    expect(screen.getByText("私有知识资产与索引状态")).toBeTruthy();
  });

  it("renders the skills page library heading", () => {
    render(
      <MemoryRouter initialEntries={["/skills"]}>
        <Routes>
          <Route path="/skills" element={<DesktopSkillsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText(/Skills/)).toBeTruthy();
  });

  it("renders the settings and onboarding flows", () => {
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <Routes>
          <Route path="*" element={<DesktopSettingsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Preference Center")).toBeTruthy();
  });

  it("renders the onboarding page", () => {
    render(
      <MemoryRouter initialEntries={["/onboarding"]}>
        <Routes>
          <Route path="/onboarding" element={<DesktopOnboardingPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("文件解析")).toBeTruthy();
  });
});
