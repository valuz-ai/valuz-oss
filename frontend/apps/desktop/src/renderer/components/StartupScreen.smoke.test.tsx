import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StartupScreen } from "./StartupScreen";

describe("StartupScreen smoke", () => {
  it("renders the headline", () => {
    const { container } = render(
      <StartupScreen
        services={[]}
        logs={[]}
        loading={true}
        error={null}
        onRetry={async () => {}}
      />,
    );
    expect(container.querySelector(".splash-title")).toBeTruthy();
    expect(container.textContent).toMatch(/VALUZ/);
  });
});
