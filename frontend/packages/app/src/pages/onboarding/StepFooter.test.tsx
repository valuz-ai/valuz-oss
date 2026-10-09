import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useRegistryStore } from "@valuz/core";
import { StepFooter } from "./StepFooter";

const registerFooterAction = (key: string, label: string) =>
  useRegistryStore.getState().registerSlot("onboarding.footer.actions", {
    id: `test-${key}`,
    key,
    component: ({ step }: { step?: string }) => (
      <button type="button">{`${label}:${step}`}</button>
    ),
  });

describe("StepFooter", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });

  it("renders the same actions row with nothing registered", () => {
    const { container } = render(
      <StepFooter
        step="connect"
        onSkip={() => {}}
        skipLabel="跳过"
        primaryLabel="继续"
        onPrimary={() => {}}
      />,
    );
    const right = container.firstElementChild?.lastElementChild;
    expect(
      Array.from(right?.children ?? []).map((el) => el.textContent),
    ).toEqual(["跳过", "继续 →"]);
  });

  it("renders a contribution for this step before Skip, with the step as context", () => {
    registerFooterAction("connect", "连接扩展");
    const { container } = render(
      <StepFooter
        step="connect"
        onSkip={() => {}}
        skipLabel="跳过"
        primaryLabel="继续"
        onPrimary={() => {}}
      />,
    );
    const right = container.firstElementChild?.lastElementChild;
    expect(
      Array.from(right?.children ?? []).map((el) => el.textContent),
    ).toEqual(["连接扩展:connect", "跳过", "继续 →"]);
  });

  it("ignores contributions keyed to another step", () => {
    registerFooterAction("team", "团队扩展");
    render(
      <StepFooter step="connect" primaryLabel="继续" onPrimary={() => {}} />,
    );
    expect(screen.queryByText(/团队扩展/)).toBeNull();
  });

  it("renders no contributions when the caller passes no step", () => {
    registerFooterAction("connect", "连接扩展");
    render(<StepFooter primaryLabel="继续" onPrimary={() => {}} />);
    expect(screen.queryByText(/连接扩展/)).toBeNull();
  });
});
