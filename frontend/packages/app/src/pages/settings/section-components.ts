import type { ComponentType } from "react";

import { createContributions } from "../../lib/contributions";

/**
 * The component behind each built-in settings section id.
 *
 * Like routes, a settings section in the edition registry is plain data; the
 * panel it shows is contributed by the plugin that owns the section and
 * withdrawn with it. A section that carries its own ``component`` (overlay and
 * edition sections) never consults this.
 */
export const sectionComponents = createContributions<ComponentType>();

export const registerSectionComponent = (
  id: string,
  Component: ComponentType,
): (() => void) => sectionComponents.register(id, Component);
