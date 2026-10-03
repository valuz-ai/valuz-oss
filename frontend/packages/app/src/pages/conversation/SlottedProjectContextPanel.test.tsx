import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import { useRegistryStore } from "@valuz/core";
import { SlottedProjectContextPanel } from "./SlottedProjectContextPanel";

beforeAll(() => {
  if (!globalThis.ResizeObserver) {
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

const ctx = { projectId: "p1", sessionId: "s1", surface: "conversation" } as const;

// Contributions echo the context they were handed, so a test can assert on it.
const Echo =
  (label: string) =>
  (props: Record<string, unknown>) => (
    <span data-testid={label}>
      {label}:{String(props.projectId)}:{String(props.sessionId)}:
      {String(props.surface)}
    </span>
  );

describe("SlottedProjectContextPanel", () => {
  beforeEach(() => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    useRegistryStore.setState({ slots: {} });
  });

  describe("with nothing registered", () => {
    it("renders no extra tab, header wrapper or generated-files action box", () => {
      const { container } = render(
        <SlottedProjectContextPanel
          {...ctx}
          generatedFiles={[]}
          projectMemory={[]}
        />,
      );
      // Built-in tabs only (project + memory).
      expect(screen.getAllByRole("tab")).toHaveLength(2);
      expect(container.querySelector("header .ml-auto")).toBeNull();
      // Regression: the empty ``<SlotRenderer/>`` used to be passed as the
      // section action, which is always truthy, so an empty ``div.pr-3`` was
      // rendered in the generated-files header.
      expect(container.querySelector("div.pr-3")).toBeNull();
    });

    it("renders the same DOM as the bare panel", async () => {
      const { ProjectDetailContextPanel } = await import("@valuz/ui");
      const slotted = render(
        <SlottedProjectContextPanel
          {...ctx}
          generatedFiles={[]}
          projectMemory={[]}
        />,
      );
      const bare = render(
        <ProjectDetailContextPanel generatedFiles={[]} projectMemory={[]} />,
      );
      // Radix derives tab / content ids from a per-render counter.
      const normalize = (html: string) => html.replace(/radix-[^"]*/g, "radix");
      expect(normalize(slotted.container.innerHTML)).toBe(
        normalize(bare.container.innerHTML),
      );
    });
  });

  describe("with contributions registered", () => {
    it("renders a tab trigger per registration and its content when selected", async () => {
      const user = userEvent.setup();
      act(() => {
        useRegistryStore.getState().registerSlot("context-panel.tabs", {
          id: "audit-tab",
          key: "audit",
          // Not a locale key: ``t`` falls back to the key itself.
          label: "Audit trail",
          component: Echo("audit"),
        });
      });
      render(
        <SlottedProjectContextPanel
          {...ctx}
          projectMemory={[]}
        />,
      );
      expect(screen.getAllByRole("tab")).toHaveLength(3);
      expect(screen.queryByTestId("audit")).toBeNull();

      await user.click(screen.getByRole("tab", { name: "Audit trail" }));
      expect(screen.getByTestId("audit").textContent).toBe(
        "audit:p1:s1:conversation",
      );
    });

    it("falls back to the registration id for the label and key", () => {
      act(() => {
        useRegistryStore.getState().registerSlot("context-panel.tabs", {
          id: "bare-tab",
          component: Echo("bare"),
        });
      });
      render(<SlottedProjectContextPanel {...ctx} />);
      expect(screen.getByRole("tab", { name: "bare-tab" })).toBeTruthy();
    });

    it("ignores a registration that reuses a built-in tab key", () => {
      act(() => {
        useRegistryStore.getState().registerSlot("context-panel.tabs", {
          id: "impostor",
          key: "memory",
          label: "Impostor",
          component: Echo("impostor"),
        });
      });
      render(<SlottedProjectContextPanel {...ctx} projectMemory={[]} />);
      expect(screen.queryByRole("tab", { name: "Impostor" })).toBeNull();
      expect(screen.getAllByRole("tab")).toHaveLength(2);
    });

    it("renders header actions with the documented context", () => {
      act(() => {
        useRegistryStore
          .getState()
          .registerSlot("context-panel.header.actions", {
            id: "hdr",
            component: Echo("hdr"),
          });
      });
      const { container } = render(<SlottedProjectContextPanel {...ctx} />);
      const wrapper = container.querySelector("header .ml-auto");
      expect(wrapper).not.toBeNull();
      expect(wrapper?.textContent).toBe("hdr:p1:s1:conversation");
    });

    it("renders sections after the generated-files section", () => {
      act(() => {
        useRegistryStore.getState().registerSlot("context-panel.sections", {
          id: "sec",
          component: Echo("sec"),
        });
      });
      render(
        <SlottedProjectContextPanel
          {...ctx}
          surface="project-home"
          sessionId={null}
          generatedFiles={[]}
        />,
      );
      const generated = screen.getByText("Artifacts");
      const section = screen.getByTestId("sec");
      expect(section.textContent).toBe("sec:p1:null:project-home");
      expect(
        generated.compareDocumentPosition(section) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it("renders generated-files actions in the section header with the file list", () => {
      const files = [
        {
          id: "f1",
          name: "report.md",
          path: "/d/report.md",
          versionNo: 1,
          isCurrent: true,
          artifactId: "A1",
        },
      ];
      act(() => {
        useRegistryStore
          .getState()
          .registerSlot("project.generatedFiles.actions", {
            id: "gen",
            component: (props: Record<string, unknown>) => (
              <button type="button">
                share {(props.generatedFiles as typeof files).length}
              </button>
            ),
          });
      });
      const { container } = render(
        <SlottedProjectContextPanel {...ctx} generatedFiles={files} />,
      );
      const box = container.querySelector("div.pr-3");
      expect(box).not.toBeNull();
      expect(box?.textContent).toBe("share 1");
    });
  });
});
