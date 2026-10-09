import type { ComponentType } from "react";

export interface SlotRegistration {
  id: string;
  component: ComponentType<Record<string, unknown>>;
  /**
   * Ordering within the slot: lower renders first (list slots) and wins
   * (single / keyed slots). Equal priorities keep registration order.
   * Defaults to 0.
   */
  priority?: number;
  /**
   * Dispatch key for keyed slots (e.g. a tool name or a node type). Ignored by
   * list slots.
   */
  key?: string;
  /**
   * Display label (an i18n key) for slots whose host draws chrome around each
   * contribution — a tab trigger, a menu heading. Ignored elsewhere.
   */
  label?: string;
}

export type SlotMap = Record<string, SlotRegistration[]>;
