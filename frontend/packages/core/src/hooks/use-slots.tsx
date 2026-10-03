import { Component, useMemo } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { useRegistryStore } from "../edition/registry-store";
import type { SlotRegistration } from "../edition/registries/slots";

interface SlotRendererProps {
  name: string;
  context?: Record<string, unknown>;
}

interface ListSlotProps extends SlotRendererProps {
  /** Render only contributions registered with this ``key`` (keyed slots). */
  slotKey?: string;
}

const _empty: SlotRegistration[] = [];

interface SlotErrorBoundaryProps {
  slot: string;
  registration: string;
  children: ReactNode;
}

/**
 * One boundary per contribution: a plugin component that throws while
 * rendering takes down only its own cell, never the host surface or the other
 * contributions next to it. The failure is logged with the slot name and the
 * contribution id so it can be traced to the plugin that registered it.
 */
class SlotErrorBoundary extends Component<SlotErrorBoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(
      `[slots] contribution "${this.props.registration}" in slot "${this.props.slot}" failed to render`,
      error,
      info.componentStack,
    );
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

function renderContribution(
  slot: string,
  registration: SlotRegistration,
  context: Record<string, unknown> | undefined,
): ReactNode {
  const Contribution = registration.component;
  return (
    <SlotErrorBoundary key={registration.id} slot={slot} registration={registration.id}>
      <Contribution {...(context ?? {})} />
    </SlotErrorBoundary>
  );
}

/**
 * The contributions in ``name`` (only those with ``key === slotKey`` when
 * given), in priority order. For hosts that draw their own chrome around each
 * contribution — tab triggers, menu groups — and render the contribution
 * itself with ``SlotContribution``.
 */
export function useSlotRegistrations(
  name: string,
  slotKey?: string,
): SlotRegistration[] {
  const registrations = useRegistryStore((s) => s.slots[name]) ?? _empty;
  return useMemo(
    () =>
      slotKey === undefined
        ? registrations
        : registrations.filter((reg) => reg.key === slotKey),
    [registrations, slotKey],
  );
}

interface SlotContributionProps {
  name: string;
  registration: SlotRegistration;
  context?: Record<string, unknown>;
}

/** One contribution, inside its own error boundary. */
export function SlotContribution({
  name,
  registration,
  context,
}: SlotContributionProps) {
  return <>{renderContribution(name, registration, context)}</>;
}

/**
 * List slot: render every contribution, in priority order — or, with
 * ``slotKey``, every contribution registered under that key.
 * OSS renders nothing (empty slots); plugins and overlays contribute via
 * `registerSlot(name, { id, component, priority?, key? })`.
 */
export function SlotRenderer({ name, context, slotKey }: ListSlotProps) {
  const registrations = useSlotRegistrations(name, slotKey);
  if (registrations.length === 0) return null;
  return <>{registrations.map((reg) => renderContribution(name, reg, context))}</>;
}

interface SingleSlotProps extends SlotRendererProps {
  /** The host's own rendering, shown while nothing occupies the slot. */
  children?: ReactNode;
}

/**
 * Single slot: the highest-priority contribution REPLACES the host's default
 * (``children``). Use it where a plugin should be able to take a whole region
 * over — a brand mark, an empty-state hero — rather than add next to it.
 */
export function SingleSlot({ name, context, children }: SingleSlotProps) {
  const winner = useRegistryStore((s) => s.slots[name]?.[0]);
  if (!winner) return <>{children ?? null}</>;
  return <>{renderContribution(name, winner, context)}</>;
}

interface KeyedSlotProps extends SlotRendererProps {
  /** Which cell to render — a tool name, a node type, a tab id. */
  slotKey: string;
  /** Rendered when no contribution claims ``slotKey``. */
  fallback?: ReactNode;
}

/**
 * Keyed slot: dispatch to the contribution registered with ``key === slotKey``
 * (highest priority wins). Unclaimed keys render ``fallback``.
 */
export function KeyedSlot({ name, slotKey, context, fallback }: KeyedSlotProps) {
  const match = useRegistryStore((s) => s.slots[name]?.find((reg) => reg.key === slotKey));
  if (!match) return <>{fallback ?? null}</>;
  return <>{renderContribution(name, match, context)}</>;
}

/**
 * Whether anything occupies ``name`` — or, with ``slotKey``, that key of a
 * keyed slot. Hosts use it to decide whether to render a separator, a menu
 * trigger, or their own fallback around a slot.
 */
export function useHasSlot(name: string, slotKey?: string): boolean {
  return useRegistryStore((s) => {
    const registrations = s.slots[name];
    if (!registrations || registrations.length === 0) return false;
    return slotKey === undefined
      ? true
      : registrations.some((reg) => reg.key === slotKey);
  });
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
