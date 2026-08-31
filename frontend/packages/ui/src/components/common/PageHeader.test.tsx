import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("groups page navigation with the title before trailing actions", () => {
    render(
      <PageHeader
        title="Playbooks"
        navigation={<button type="button">Templates</button>}
        action={<button type="button">Create</button>}
      />,
    );

    const header = screen.getByText("Playbooks").closest("[data-slot=page-header]");
    expect(header).toBeTruthy();
    expect(header?.children).toHaveLength(2);
    expect(header?.children[0]?.textContent).toContain("Templates");
    expect(header?.children[1]?.textContent).toContain("Create");
  });
});
