import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { initI18n } from "@valuz/shared/i18n";
import { agentsApi, projectsApi, useRegistryStore } from "@valuz/core";
import { PlatformProvider } from "@valuz/app/platform";
import type { PlatformCapabilities } from "@valuz/core";
import { ProjectsPage } from "./ProjectsPage";

// The page hands its header to the layout; capture it so a test can render it.
let latestHeader: ReactNode | null = null;

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useOutletContext: () => ({
      setRightPanel: vi.fn(),
      setHeader: (node: ReactNode | null) => {
        latestHeader = node;
      },
      setHeaderClassName: vi.fn(),
      setHideHeader: vi.fn(),
      setAsideClassName: vi.fn(),
      setMainClassName: vi.fn(),
      setContentInnerClassName: vi.fn(),
    }),
  };
});

const platform: PlatformCapabilities = {
  selectDirectory: vi.fn(),
  copyFiles: vi.fn(),
  deleteFile: vi.fn(),
  revealInFinder: vi.fn(),
  quitApp: vi.fn(),
  openNewWindow: vi.fn(),
  isElectron: false,
  isMac: false,
};

describe("ProjectsPage", () => {
  beforeEach(() => {
    latestHeader = null;
    useRegistryStore.setState({ slots: {} });
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    vi.spyOn(projectsApi, "list").mockResolvedValue({ projects: [] });
    vi.spyOn(agentsApi, "listAgents").mockResolvedValue({ agents: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates managed cloud projects without showing or sending a local directory", async () => {
    const create = vi.spyOn(projectsApi, "create").mockResolvedValue({
      id: "p1",
      name: "Cloud",
      kind: "project",
      root_path: null,
      cwd: null,
      icon: null,
      instructions_md: "",
    });

    render(
      <MemoryRouter>
        <PlatformProvider value={platform}>
          <ProjectsPage directoryFieldMode="managed" />
        </PlatformProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("No projects, click create")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(screen.getByText(/managed directory/)).toBeTruthy();
    expect(screen.queryByText("Select directory")).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("my-project"), {
      target: { value: "Cloud" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Create" }).at(-1)!);

    await waitFor(() => {
      // Second arg is the execution-target opts — undefined on
      // single-target builds (no targets registered in this test).
      expect(create).toHaveBeenCalledWith({ name: "Cloud" }, undefined);
    });
  });
  describe("resource.project.list.actions", () => {
    const renderHeader = async () => {
      render(
        <MemoryRouter>
          <PlatformProvider value={platform}>
            <ProjectsPage />
          </PlatformProvider>
        </MemoryRouter>,
      );
      await waitFor(() => {
        expect(latestHeader).not.toBeNull();
      });
      return render(<MemoryRouter>{latestHeader}</MemoryRouter>);
    };

    it("passes the create menu through unwrapped when nothing is registered", async () => {
      await renderHeader();
      const create = screen.getAllByRole("button", { name: "Create" }).at(-1)!;
      // PageHeader's own ``shrink-0`` action box holds the menu trigger
      // directly — no extra flex row around it.
      expect(create.parentElement?.className).toBe("shrink-0");
    });

    it("renders registered contributions beside the create menu with the navigate context", async () => {
      useRegistryStore.getState().registerSlot("resource.project.list.actions", {
        id: "plugin-action",
        component: (props: Record<string, unknown>) => (
          <button type="button" data-testid="plugin-action">
            {typeof props.navigate}
          </button>
        ),
      });
      await renderHeader();
      const pluginAction = screen.getByTestId("plugin-action");
      expect(pluginAction.textContent).toBe("function");
      const create = screen.getAllByRole("button", { name: "Create" }).at(-1)!;
      expect(pluginAction.parentElement).toBe(create.parentElement);
      expect(pluginAction.parentElement?.className).toContain("gap-2");
    });
  });
});
