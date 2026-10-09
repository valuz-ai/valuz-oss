/** @vitest-environment jsdom */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import type {
  ImportProjectConfirmResult,
  ImportProjectPreview,
} from "@valuz/core";

import { ImportProjectDialog } from "./ImportProjectDialog";

const basePreview: ImportProjectPreview = {
  preview_id: "pv1",
  name_conflict: false,
  project: {
    name: "Demo",
    kind: "project",
    icon: null,
    has_instructions: false,
    has_memory: false,
    memory_file_count: 0,
  },
  members: [
    {
      agent_slug: "a1",
      source_agent_slug: null,
      name: "Agent One",
      description: "",
      in_library: true,
    },
  ],
  automations: [],
  skills: [],
  connectors: [],
};

const conflictPreview: ImportProjectPreview = {
  ...basePreview,
  preview_id: "pv2",
  name_conflict: true,
};

const createdResult: ImportProjectConfirmResult = {
  status: "created",
  project: {
    id: "p1",
    name: "Demo",
    kind: "project",
    root_path: null,
    cwd: null,
    icon: null,
  },
  members_created: 1,
  members_reused: 0,
  agents_created: 1,
  agents_skipped: 0,
  automations_created: 0,
  automation_errors: [],
  connectors_to_configure: [],
};

const previewMock = vi.fn();
const confirmMock = vi.fn();

vi.mock("@valuz/core", async () => {
  const actual = await vi.importActual<typeof import("@valuz/core")>(
    "@valuz/core",
  );
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      importProjectPreview: (file: File) => previewMock(file),
      importProjectConfirm: (id: string) => confirmMock(id),
    },
  };
});

beforeAll(() => initI18n({ locale: "en-US", fallbackLocale: "en-US" }));

describe("ImportProjectDialog", () => {
  it("shows the name-conflict banner and disables confirm when name_conflict=true", async () => {
    previewMock.mockResolvedValue(conflictPreview);
    render(
      <ImportProjectDialog
        file={new File(["x"], "demo.valuzpack")}
        open
        onOpenChange={() => {}}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText("A project with this name already exists"),
      ).toBeTruthy(),
    );
    const confirm = screen.getByText("Confirm import").closest("button");
    expect(confirm?.hasAttribute("disabled")).toBe(true);
  });

  it("on confirm calls importProjectConfirm and invokes onImported when status===created", async () => {
    previewMock.mockResolvedValue(basePreview);
    confirmMock.mockResolvedValue(createdResult);
    const onImported = vi.fn();
    render(
      <ImportProjectDialog
        file={new File(["x"], "demo.valuzpack")}
        open
        onOpenChange={() => {}}
        onImported={onImported}
      />,
    );
    await waitFor(() => expect(screen.getByText("Agent One")).toBeTruthy());
    const confirm = screen.getByText("Confirm import").closest("button")!;
    await act(async () => {
      fireEvent.click(confirm);
    });
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith("pv1"));
    await waitFor(() => expect(onImported).toHaveBeenCalled());
    const arg = onImported.mock.calls[0][0] as { id: string };
    expect(arg.id).toBe("p1");
  });

  it("shows memory failures even when the project itself was created", async () => {
    previewMock.mockResolvedValue(basePreview);
    confirmMock.mockResolvedValue({
      ...createdResult,
      memory_imported: 1,
      memory_errors: [
        {
          entry_index: 1,
          error_code: "memory.protected",
          error: "The source was previously forgotten.",
        },
      ],
    });
    render(
      <ImportProjectDialog
        file={new File(["x"], "demo.valuzpack")}
        open
        onOpenChange={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByText("Agent One")).toBeTruthy());
    await act(async () => {
      fireEvent.click(screen.getByText("Confirm import").closest("button")!);
    });
    await waitFor(() =>
      expect(
        screen.getByText(
          "Some content could not be imported; the remaining content was kept:",
        ),
      ).toBeTruthy(),
    );
    expect(screen.getByText(/The source was previously forgotten/)).toBeTruthy();
  });
});
