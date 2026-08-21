import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type {
  A2UIGalleryExtensionGroup,
  A2UIGalleryExtensionSection,
  A2UIGalleryExtensionViewProps,
} from "./types";

type Listener = () => void;

export interface RegisteredA2UIGalleryExtensionSection
  extends A2UIGalleryExtensionSection {
  View: LazyExoticComponent<ComponentType<A2UIGalleryExtensionViewProps>>;
}

export interface RegisteredA2UIGalleryExtensionGroup
  extends Omit<A2UIGalleryExtensionGroup, "sections"> {
  sections: RegisteredA2UIGalleryExtensionSection[];
}

interface Registration {
  source: A2UIGalleryExtensionGroup;
  group: RegisteredA2UIGalleryExtensionGroup;
}

const groups = new Map<string, Registration>();
const listeners = new Set<Listener>();
let snapshot: A2UIGalleryExtensionGroup[] = [];
let registeredSnapshot: RegisteredA2UIGalleryExtensionGroup[] = [];

function publish() {
  const registrations = [...groups.values()];
  snapshot = registrations.map(({ source }) => source);
  registeredSnapshot = registrations.map(({ group }) => group);
  listeners.forEach((listener) => listener());
}

function assertGroup(group: A2UIGalleryExtensionGroup) {
  if (!group.id.trim()) {
    throw new Error("Gallery extension group id is required");
  }
  if (!group.label.trim()) {
    throw new Error(`Gallery extension group "${group.id}" needs a label`);
  }
  if (group.sections.length === 0) {
    throw new Error(`Gallery extension group "${group.id}" needs at least one section`);
  }
  const sectionIds = new Set<string>();
  group.sections.forEach((section) => {
    if (!section.id.trim()) {
      throw new Error(
        `Gallery extension group "${group.id}" has an empty section id`,
      );
    }
    if (sectionIds.has(section.id)) {
      throw new Error(`Gallery extension group "${group.id}" has duplicate section "${section.id}"`);
    }
    if (!Number.isFinite(section.componentCount) || section.componentCount < 0) {
      throw new Error(`Gallery extension section "${group.id}/${section.id}" has an invalid component count`);
    }
    sectionIds.add(section.id);
  });
}

/**
 * Register one distribution-owned Gallery menu group.
 *
 * Re-registering the same id replaces the previous contribution, which keeps
 * Vite HMR and edition plugin activation deterministic. The returned disposer
 * only removes the exact registration it created.
 */
export function registerA2UIGalleryExtension(
  group: A2UIGalleryExtensionGroup,
): () => void {
  assertGroup(group);
  const registered = {
    ...group,
    sections: group.sections.map((section) => ({
      ...section,
      View: lazy(section.load),
    })),
  };
  groups.set(group.id, { source: group, group: registered });
  publish();
  return () => {
    if (groups.get(group.id)?.source !== group) return;
    groups.delete(group.id);
    publish();
  };
}

export function getA2UIGalleryExtensions(): A2UIGalleryExtensionGroup[] {
  return snapshot;
}

export function getRegisteredA2UIGalleryExtensions():
  RegisteredA2UIGalleryExtensionGroup[] {
  return registeredSnapshot;
}

export function subscribeA2UIGalleryExtensions(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test-only reset; kept out of the package barrel. */
export function resetA2UIGalleryExtensionsForTests() {
  groups.clear();
  publish();
}
