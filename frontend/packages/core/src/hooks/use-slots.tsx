import { Fragment } from "react";
import { useRegistryStore } from "../edition/registry-store";
import type { SlotRegistration } from "../edition/registries/slots";

interface SlotRendererProps {
  name: string;
  context?: Record<string, unknown>;
}

/**
 * Render all components registered for a named slot.
 * OSS renders nothing (empty slots); overlays register components via
 * `useRegistryStore.getState().registerSlot(name, { id, component })`.
 */
const _empty: SlotRegistration[] = [];

export function SlotRenderer({ name, context }: SlotRendererProps) {
  const registrations = useRegistryStore((s) => s.slots[name]) ?? _empty;
  if (registrations.length === 0) return null;
  return (
    <>
      {registrations.map((reg) => (
        <Fragment key={reg.id}>
          <reg.component {...(context ?? {})} />
        </Fragment>
      ))}
    </>
  );
}

/**
 * True when an overlay has suppressed this surface.
 *
 * Hosts call it where they render a surface an overlay may need to take over
 * (``conversation.composer`` during a share selection, for example) and skip
 * their own render when it returns true.
 */
export function useSurfaceSuppressed(surface: string): boolean {
  return useRegistryStore((s) => s.suppressed[surface] ?? false);
}
