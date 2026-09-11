import { Fragment, useMemo } from "react";
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

const _emptyNames: ReadonlySet<string> = new Set();

/**
 * The set of slot names an overlay has registered something for.
 *
 * ``SlotRenderer`` renders nothing for an empty slot, which is right when the
 * host wants "overlay content here, or nothing". It is wrong when the host has
 * its OWN rendering to fall back to: ``return <SlotRenderer …/>`` commits to
 * the slot and silently swallows the fallback whenever no overlay registered.
 *
 * Hosts that own a default call this once, at the top of the component (it is a
 * hook — it cannot go inside a render callback), and branch on membership:
 *
 * ```tsx
 * const slots = useRegisteredSlotNames();
 * // …later, inside a plain render helper:
 * if (slots.has(key)) return <SlotRenderer name={key} context={{ … }} />;
 * return <MyDefault />;
 * ```
 *
 * Returns the same frozen empty set while nothing is registered, so a host that
 * subscribes in a hot path does not re-render on every store write.
 */
export function useRegisteredSlotNames(): ReadonlySet<string> {
  const slots = useRegistryStore((s) => s.slots);
  // Memoized on the store's own object identity: the registry replaces
  // ``slots`` wholesale on every register/unregister and leaves it alone
  // otherwise, so this rebuilds exactly when the answer can have changed. A
  // fresh Set per render would defeat any consumer that puts this in a
  // dependency array.
  return useMemo(() => {
    // ``length > 0`` matters: unregistering the last component for a name can
    // leave the key behind with an empty array, and that must read as "no
    // overlay here" — otherwise the host skips its own fallback forever.
    const names = Object.keys(slots).filter((n) => (slots[n]?.length ?? 0) > 0);
    return names.length === 0 ? _emptyNames : new Set(names);
  }, [slots]);
}
