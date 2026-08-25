import { createElement } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ResourceCategory } from "@valuz/shared";
import { bucketByCategory, CategorizedList } from "./CategorizedList";

interface ResourceRow {
  rowId: string;
  slug: string;
  source: "agents" | "codex";
  organizationId: string | null;
}

describe("bucketByCategory", () => {
  it("shows one logical organization resource while preserving every source row", () => {
    const rows: ResourceRow[] = [
      {
        rowId: "agents:opscli-agent",
        slug: "opscli-agent",
        source: "agents",
        organizationId: "org-resource-1",
      },
      {
        rowId: "codex:opscli-agent",
        slug: "opscli-agent",
        source: "codex",
        organizationId: "org-resource-1",
      },
    ];
    const categories = [
      {
        id: "organization",
        label: "Organization",
        order: 0,
        multiAssign: true,
        filter: (row: ResourceRow) => row.organizationId !== null,
        groupBy: (row: ResourceRow) => row.organizationId ?? row.rowId,
      },
      {
        id: "agents",
        label: "Agents",
        order: 1,
        filter: (row: ResourceRow) => row.source === "agents",
      },
      {
        id: "codex",
        label: "Codex",
        order: 2,
        filter: (row: ResourceRow) => row.source === "codex",
      },
    ] as Array<
      ResourceCategory<ResourceRow> & {
        groupBy?: (row: ResourceRow) => string;
      }
    >;

    const buckets = bucketByCategory(rows, categories, (row) => row.rowId);

    expect(
      buckets.find((bucket) => bucket.category.id === "organization")?.items,
    ).toEqual([rows[0]]);
    expect(
      buckets.find((bucket) => bucket.category.id === "agents")?.items,
    ).toEqual([rows[0]]);
    expect(
      buckets.find((bucket) => bucket.category.id === "codex")?.items,
    ).toEqual([rows[1]]);
  });

  it("does not send a multi-assigned-only row to Other", () => {
    const organizationOnly: ResourceRow = {
      rowId: "cloud:team-search",
      slug: "team-search",
      source: "agents",
      organizationId: "org-resource-1",
    };
    const categories: ResourceCategory<ResourceRow>[] = [
      {
        id: "organization",
        label: "Organization",
        order: 0,
        multiAssign: true,
        filter: (row) => row.organizationId !== null,
      },
      {
        id: "installed",
        label: "Installed",
        order: 1,
        filter: () => false,
      },
    ];

    const buckets = bucketByCategory(
      [organizationOnly],
      categories,
      (row) => row.rowId,
    );

    expect(buckets).toHaveLength(1);
    expect(buckets[0]).toEqual({
      category: categories[0],
      items: [organizationOnly],
    });
  });

  it("highlights the organization card when a namesake source row is selected", () => {
    const rows: ResourceRow[] = [
      {
        rowId: "agents:opscli-agent",
        slug: "opscli-agent",
        source: "agents",
        organizationId: "org-resource-1",
      },
      {
        rowId: "codex:opscli-agent",
        slug: "opscli-agent",
        source: "codex",
        organizationId: "org-resource-1",
      },
    ];
    const categories: ResourceCategory<ResourceRow>[] = [
      {
        id: "organization",
        label: "Organization",
        order: 0,
        multiAssign: true,
        filter: (row) => row.organizationId !== null,
        groupBy: (row) => row.organizationId ?? row.rowId,
      },
      {
        id: "agents",
        label: "Agents",
        order: 1,
        filter: (row) => row.source === "agents",
      },
      {
        id: "codex",
        label: "Codex",
        order: 2,
        filter: (row) => row.source === "codex",
      },
    ];

    render(
      createElement(CategorizedList<ResourceRow>, {
        items: rows,
        categories,
        selectedId: rows[1].rowId,
        getId: (row) => row.rowId,
        onSelect: () => undefined,
        renderItem: (row, selected) =>
          createElement(
            "span",
            { "data-testid": row.rowId },
            selected ? "selected" : "idle",
          ),
      }),
    );

    expect(
      screen.getAllByTestId("agents:opscli-agent").map((node) => node.textContent),
    ).toEqual(["selected", "idle"]);
    expect(screen.getByTestId("codex:opscli-agent").textContent).toBe(
      "selected",
    );
  });
});
