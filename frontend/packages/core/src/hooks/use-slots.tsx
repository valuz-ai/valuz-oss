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
