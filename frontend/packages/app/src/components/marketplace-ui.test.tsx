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

  it("names what a self-published item is rather than calling it official", () => {
    render(
      <>
        <MarketplaceSourcePill source="valuz_official" itemType="skill" />
        <MarketplaceSourcePill source="valuz_official" itemType="connector" />
        <MarketplaceSourcePill source="valuz_official" itemType="agent_team_template" />
      </>,
    );
    // Labels are the zh-CN ones: that is the locale these tests render under.
    expect(screen.getByText("技能")).not.toBeNull();
    expect(screen.getByText("连接器")).not.toBeNull();
    expect(screen.getByText("Agent 团队")).not.toBeNull();
    expect(screen.queryByText("Valuz 官方")).toBeNull();
  });

  it("still names the store an ingested item came from", () => {
    // Only our own uploads swap provenance for type — a skill crawled from
    // SkillHub still says SkillHub, which is the useful fact about it.
    render(<MarketplaceSourcePill source="skillhub" itemType="skill" />);
    expect(screen.getByText("SkillHub")).not.toBeNull();
    expect(screen.queryByText("技能")).toBeNull();
  });

  it("falls back to the source label when the caller has no item type", () => {
    render(<MarketplaceSourcePill source="valuz_official" />);
    expect(screen.getByText("Valuz 官方")).not.toBeNull();
  });

  it("never crashes on a badge value it does not know", () => {
    render(<MarketplaceBadgePill badge={"shiny_new" as never} />);
    expect(screen.getByText("Shiny new")).not.toBeNull();
  });
});
