import { SlotRenderer } from "@valuz/core";

type ResourceType = "agent" | "skill" | "connector" | "kb";

interface ResourceActionSlotProps {
  resourceType: ResourceType;
  resource: Record<string, unknown>;
}

/**
 * Resource card action button slot.
 * OSS renders nothing; the commercial overlay registers components
 * via `registerSlot("resource.{type}.actions", { id, component })`
 * to inject sync / permission / approval buttons.
 */
export function ResourceActionSlot({
  resourceType,
  resource,
}: ResourceActionSlotProps) {
  return (
    <SlotRenderer
      name={`resource.${resourceType}.actions`}
      context={{ resourceType, resource }}
    />
  );
}

/**
 * Badges rendered right after a resource's NAME in list rows — the same line
 * as the built-in badge, before any counts.
 *
 * Separate from the action slot on purpose: that one sits in the row's action
 * area (icons, menus), which is the wrong place for a word that qualifies
 * *what this row is*. OSS renders nothing; overlays register components via
 * `registerSlot("resource.{type}.title.badges", { id, component })`.
 */
export function ResourceTitleBadgeSlot({
  resourceType,
  resource,
}: ResourceActionSlotProps) {
  return (
    <SlotRenderer
      name={`resource.${resourceType}.title.badges`}
      context={{ resourceType, resource }}
    />
  );
}

/**
 * Resource detail-header action slot.
 *
 * Kept separate from the list-row slot so overlays can expose an explicit,
 * labelled action on a detail page without adding an icon to every list row.
 */
export function ResourceDetailActionSlot({
  resourceType,
  resource,
}: ResourceActionSlotProps) {
  return (
    <SlotRenderer
      name={`resource.${resourceType}.detail.actions`}
      context={{ resourceType, resource }}
    />
  );
}

/**
 * Overlay contributions rendered inside the host-owned Copy dropdown.
 *
 * The native Copy handler always remains the first item. The host only
 * switches from its original one-click control to a dropdown when this slot
 * has at least one registration.
 */
export function ResourceCopyMenuItemSlot({
  resourceType,
  resource,
}: ResourceActionSlotProps) {
  return (
    <SlotRenderer
      name={`resource.${resourceType}.copy.menu-items`}
      context={{ resourceType, resource }}
    />
  );
}
