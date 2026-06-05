import type { ComponentType } from "react";

export interface SlotRegistration {
  id: string;
  component: ComponentType<Record<string, unknown>>;
}

export type SlotMap = Record<string, SlotRegistration[]>;
