import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  MarketplaceBadgePill,
  MarketplaceSourcePill,
  humanizeWireValue,
} from "./marketplace-ui";

describe("marketplace-ui open wire values", () => {
  it("humanizes wire values this build has no label for", () => {
    expect(humanizeWireValue("brand-new_store")).toBe("Brand new store");
    expect(humanizeWireValue("plugin")).toBe("Plugin");
    expect(humanizeWireValue("")).toBe("");
  });

  it("renders a known source with its label and an unknown one generically", () => {
    render(
      <>
        <MarketplaceSourcePill source="skillhub" />
        <MarketplaceSourcePill source="brand-new-store" />
      </>,
    );
    expect(screen.getByText("SkillHub")).not.toBeNull();
    expect(screen.getByText("Brand new store")).not.toBeNull();
  });

  it("never crashes on a badge value it does not know", () => {
    render(<MarketplaceBadgePill badge={"shiny_new" as never} />);
    expect(screen.getByText("Shiny new")).not.toBeNull();
  });
});
