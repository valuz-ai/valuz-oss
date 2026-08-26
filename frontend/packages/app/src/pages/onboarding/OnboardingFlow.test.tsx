import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { initI18n } from "@valuz/shared/i18n";

// vi.hoisted so the (hoisted) vi.mock factories below can reference these.
const {
  navigateMock,
  markOnboardedMock,
  createExampleProjectMock,
  createAssistantMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  markOnboardedMock: vi.fn(),
  createExampleProjectMock: vi.fn(),
  createAssistantMock: vi.fn(),
}));

// Mock the heavy step children to focus on the orchestrator's step machine —
// ConnectStep/TeamStep hit real APIs on mount, which is tested elsewhere.
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useNavigate: () => navigateMock,
}));
vi.mock("../../lib/onboarding", () => ({ markOnboarded: markOnboardedMock }));
vi.mock("@valuz/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@valuz/core")>()),
  onboardingApi: {
    createExampleProject: createExampleProjectMock,
    createAssistant: createAssistantMock,
  },
}));
vi.mock("./WelcomeStep", () => ({
  WelcomeStep: ({ onStart }: { onStart: () => void }) => (
    <button onClick={onStart}>mock-welcome-start</button>
  ),
}));
vi.mock("./ConnectStep", () => ({
  ConnectStep: ({ onContinue }: { onContinue: () => void }) => (
    <button onClick={onContinue}>mock-connect-continue</button>
  ),
}));
vi.mock("./TeamStep", () => ({
  TeamStep: ({ onEnter }: { onEnter: (id: string) => Promise<void> }) => (
    <button onClick={() => void onEnter("content")}>mock-team-enter</button>
  ),
}));

import { OnboardingFlow } from "./OnboardingFlow";

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" }));
beforeEach(() => {
  navigateMock.mockClear();
  markOnboardedMock.mockClear();
  createExampleProjectMock.mockReset();
  createExampleProjectMock.mockResolvedValue({
    project_id: "ws-1",
    project_name: "示例项目",
  });
  createAssistantMock.mockReset();
  createAssistantMock.mockResolvedValue({ agent_slug: "valurion" });
});

describe("OnboardingFlow", () => {
  it("should start on the welcome step", () => {
    render(<OnboardingFlow />);
    expect(screen.getByText("mock-welcome-start")).toBeTruthy();
  });

  it("should advance welcome → connect → team", () => {
    render(<OnboardingFlow />);
    fireEvent.click(screen.getByText("mock-welcome-start"));
    expect(screen.getByText("mock-connect-continue")).toBeTruthy();
    fireEvent.click(screen.getByText("mock-connect-continue"));
    expect(screen.getByText("mock-team-enter")).toBeTruthy();
  });

  it("should create the example project and route into it when a team is entered", async () => {
    render(<OnboardingFlow />);
    fireEvent.click(screen.getByText("mock-welcome-start"));
    fireEvent.click(screen.getByText("mock-connect-continue"));
    fireEvent.click(screen.getByText("mock-team-enter"));
    await waitFor(() =>
      expect(createExampleProjectMock).toHaveBeenCalledWith("content"),
    );
    expect(markOnboardedMock).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith("/projects/ws-1");
  });

  it("should seed the Valuz helper, mark onboarded, and go home when the guide is skipped", async () => {
    render(<OnboardingFlow />);
    fireEvent.click(screen.getByText("跳过引导"));
    // Skip is best-effort: it seeds the default assistant so the user never
    // lands in an empty library, then finishes.
    await waitFor(() => expect(createAssistantMock).toHaveBeenCalled());
    expect(markOnboardedMock).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith("/");
  });

  it("should still finish when seeding the helper fails (no model channel)", async () => {
    // _ensure_valuz_helper 422s when no provider is configured. The skip must
    // swallow that and finish anyway — never trap the user in onboarding.
    createAssistantMock.mockRejectedValue(new Error("422 no channel"));
    render(<OnboardingFlow />);
    fireEvent.click(screen.getByText("跳过引导"));
    await waitFor(() => expect(markOnboardedMock).toHaveBeenCalled());
    expect(navigateMock).toHaveBeenCalledWith("/");
  });
});
