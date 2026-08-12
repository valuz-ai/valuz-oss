import type { ReactComponentImplementation } from "@a2ui/react/v0_9";

import { valuzBaseComponents } from "./base";

export interface RegisterA2UIResult {
  accepted: string[];
  rejected: Array<{ name: string; reason: string }>;
}

const extensions = new Map<string, ReactComponentImplementation[]>();
const listeners = new Set<() => void>();
let version = 0;

function notify(): void {
  version += 1;
  for (const listener of listeners) listener();
}

export function registerA2UIComponents(
  source: string,
  components: ReactComponentImplementation[],
): RegisterA2UIResult {
  const occupied = new Set(valuzBaseComponents.map((component) => component.name));
  for (const [registeredSource, registered] of extensions) {
    if (registeredSource === source) continue;
    for (const component of registered) occupied.add(component.name);
  }

  const accepted: ReactComponentImplementation[] = [];
  const rejected: RegisterA2UIResult["rejected"] = [];
  const localNames = new Set<string>();
  for (const component of components) {
    const name = component.name.trim();
    if (!name) {
      rejected.push({ name, reason: "component name is empty" });
    } else if (occupied.has(name) || localNames.has(name)) {
      rejected.push({ name, reason: "component name already exists" });
    } else {
      accepted.push(component);
      localNames.add(name);
    }
  }

  extensions.set(source, accepted);
  notify();
  return { accepted: accepted.map((component) => component.name), rejected };
}

export function unregisterA2UIComponents(source: string): void {
  if (!extensions.delete(source)) return;
  notify();
}

export function effectiveA2UIComponents(): ReactComponentImplementation[] {
  return [
    ...valuzBaseComponents,
    ...Array.from(extensions.values()).flat(),
  ];
}

export function effectiveA2UIComponentNames(): string[] {
  return effectiveA2UIComponents().map((component) => component.name);
}

export function getA2UIRegistryVersion(): number {
  return version;
}

export function subscribeA2UIComponents(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetA2UIComponentsForTests(): void {
  if (extensions.size === 0) return;
  extensions.clear();
  notify();
}
