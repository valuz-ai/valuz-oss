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
