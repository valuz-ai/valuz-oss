import type { ComponentType } from "react";

export type A2UIGalleryTheme = "light" | "dark";

export interface A2UIGalleryExtensionViewProps {
  /** Preview theme selected in the shared Gallery chrome. */
  theme: A2UIGalleryTheme;
  /** Whether examples should exercise their compact/narrow layout. */
  narrow: boolean;
  /** Current Gallery search text. Extension views own their filtering logic. */
  query: string;
}

export interface A2UIGalleryExtensionModule {
  default: ComponentType<A2UIGalleryExtensionViewProps>;
}

export interface A2UIGalleryExtensionSection {
  id: string;
  label: string;
  description: string;
  /** Used by the shared menu and aggregate count without eagerly loading code. */
  componentCount: number;
  /**
   * Load the section only after the user opens it. Distribution packages keep
   * their catalogs, fixtures, and rendering dependencies out of the base app
   * chunk by providing a dynamic import here.
   */
  load: () => Promise<A2UIGalleryExtensionModule>;
}

export interface A2UIGalleryExtensionGroup {
  id: string;
  label: string;
  description: string;
  sections: A2UIGalleryExtensionSection[];
}
