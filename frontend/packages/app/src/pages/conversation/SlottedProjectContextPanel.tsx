import { useMemo } from "react";
import type { ComponentProps } from "react";
import {
  SlotContribution,
  SlotRenderer,
  useHasSlot,
  useSlotRegistrations,
  useTranslation,
} from "@valuz/core";
import { ProjectDetailContextPanel } from "@valuz/ui";

type ProjectDetailContextPanelProps = ComponentProps<
  typeof ProjectDetailContextPanel
>;

/** Which host surface a context panel is mounted on (slot context). */
export type ContextPanelSurface = "conversation" | "project-home";

type SlottedProjectContextPanelProps = Omit<
  ProjectDetailContextPanelProps,
  "extraTabs" | "headerActions" | "extraSections" | "generatedFilesAction"
> & {
  projectId: string | null;
  sessionId: string | null;
  surface: ContextPanelSurface;
};

/**
 * ``ProjectDetailContextPanel`` with the context-panel extension points wired.
 *
 * ``@valuz/ui`` cannot import the slot registry, so the panel takes plain
 * ``extraTabs`` / ``headerActions`` / ``extraSections`` / ``generatedFilesAction``
 * props and this app-level wrapper fills them from the registry. Every slot is
 * passed ``undefined`` — never an element that renders nothing — while it is
 * empty: the panel branches on those props' truthiness (the header wrapper, the
 * section action's ``pr-3`` box), so an always-truthy ``<SlotRenderer/>`` would
 * leave an empty box behind.
 *
 * The registry subscription lives here rather than in the host that builds the
 * panel node, so a plugin registering late re-renders this panel only — not the
 * conversation page or the project home — and the hosts' memo / effect
 * dependency lists need no registry state.
 */
export function SlottedProjectContextPanel({
  projectId,
  sessionId,
  surface,
  ...panelProps
}: SlottedProjectContextPanelProps) {
  const { t } = useTranslation();
  const tabRegistrations = useSlotRegistrations("context-panel.tabs");
  const hasHeaderActions = useHasSlot("context-panel.header.actions");
  const hasSections = useHasSlot("context-panel.sections");
  const hasGeneratedFilesActions = useHasSlot("project.generatedFiles.actions");

  const context = useMemo(
    () => ({ projectId, sessionId, surface }),
    [projectId, sessionId, surface],
  );

  const extraTabs = useMemo(() => {
    const seen = new Set<string>();
    const tabs: NonNullable<ProjectDetailContextPanelProps["extraTabs"]> = [];
    for (const registration of tabRegistrations) {
      const id = registration.key ?? registration.id;
      // Registrations arrive in priority order; a repeated tab id would give
      // Radix two triggers for one value, so the higher-priority one wins.
      if (seen.has(id)) continue;
      seen.add(id);
      tabs.push({
        id,
        label: t(
          (registration.label ?? registration.id) as Parameters<typeof t>[0],
        ),
        content: (
          <SlotContribution
            name="context-panel.tabs"
            registration={registration}
            context={context}
          />
        ),
      });
    }
    return tabs;
  }, [tabRegistrations, context, t]);

  return (
    <ProjectDetailContextPanel
      {...panelProps}
      extraTabs={extraTabs.length > 0 ? extraTabs : undefined}
      headerActions={
        hasHeaderActions ? (
          <SlotRenderer name="context-panel.header.actions" context={context} />
        ) : undefined
      }
      extraSections={
        hasSections ? (
          <SlotRenderer name="context-panel.sections" context={context} />
        ) : undefined
      }
      generatedFilesAction={
        hasGeneratedFilesActions ? (
          <SlotRenderer
            name="project.generatedFiles.actions"
            context={{ generatedFiles: panelProps.generatedFiles }}
          />
        ) : undefined
      }
    />
  );
}
