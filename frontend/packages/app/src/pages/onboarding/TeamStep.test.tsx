import { beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { initI18n } from "@valuz/shared/i18n";
import { TeamStep } from "./TeamStep";

// The onboarding chrome is i18n'd; init once so assertions see real copy.
beforeAll(() => {
  initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
});

const renderStep = (onEnter = vi.fn().mockResolvedValue(undefined)) => {
  const onAssistant = vi.fn().mockResolvedValue(undefined);
  const onSkip = vi.fn();
  const onBackToConnect = vi.fn();
  render(
    <TeamStep
      onEnter={onEnter}
      onAssistant={onAssistant}
      onSkip={onSkip}
      onBackToConnect={onBackToConnect}
    />,
  );
  return { onEnter, onAssistant, onSkip, onBackToConnect };
};

describe("TeamStep", () => {
  it("should show the three preset teams and the no-team block when on the grid", () => {
    renderStep();
    expect(screen.getByText("通用 Team")).toBeTruthy();
    expect(screen.getByText("投研 Team")).toBeTruthy();
    expect(screen.getByText("产研 Team")).toBeTruthy();
    expect(screen.getByText("暂时不需要 team")).toBeTruthy();
  });

  it("should call onAssistant when picking the no-team block", () => {
    const { onAssistant } = renderStep();
    fireEvent.click(screen.getByText("暂时不需要 team"));
    expect(onAssistant).toHaveBeenCalled();
    // It's not a team preset — no preset detail / deploy button appears.
    expect(screen.queryByRole("button", { name: /进入示例项目/ })).toBeNull();
  });

  it("should reveal a preset's role briefs and deploy button when picked", () => {
    renderStep();
    fireEvent.click(screen.getByText("投研 Team"));
    // 投研 roster (from mock) — lead + a second role are rendered
    expect(screen.getByText("行业分析师")).toBeTruthy();
    expect(screen.getByText("财务建模师")).toBeTruthy();
    expect(screen.getByRole("button", { name: /进入示例项目/ })).toBeTruthy();
  });

  it("should call onEnter with the picked team id when entering the example project", async () => {
    const { onEnter } = renderStep();
    fireEvent.click(screen.getByText("产研 Team"));
    fireEvent.click(screen.getByRole("button", { name: /进入示例项目/ }));
    await waitFor(() => expect(onEnter).toHaveBeenCalledWith("product"));
  });

  it("should call onSkip when skipping the team step", () => {
    const { onSkip } = renderStep();
    fireEvent.click(screen.getByRole("button", { name: /暂时跳过/ }));
    expect(onSkip).toHaveBeenCalled();
  });
});
