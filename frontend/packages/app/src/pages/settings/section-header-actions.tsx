import type { ReactNode } from "react";
import { SlotRenderer, useHasSlot } from "@valuz/core";

const SECTION_HEADER_ACTIONS_SLOT = "settings.section.header.actions";

/**
 * The `settings.section.header.actions` contributions for one settings section
 * (keyed by section id), ready to hand to ``SettingsSection``'s ``actions``.
 *
 * Returns ``undefined`` while nothing is registered for the key, so the section
 * keeps its original title block: ``SettingsSection`` branches on the prop, and
 * a bare ``<SlotRenderer/>`` element is always truthy.
 */
export const useSectionHeaderActions = (
  sectionId: string,
): ReactNode | undefined => {
  const occupied = useHasSlot(SECTION_HEADER_ACTIONS_SLOT, sectionId);
  return occupied ? (
    <SlotRenderer name={SECTION_HEADER_ACTIONS_SLOT} slotKey={sectionId} />
  ) : undefined;
};
