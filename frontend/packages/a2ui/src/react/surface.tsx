import { A2uiSurface, type ReactComponentImplementation } from "@a2ui/react/v0_9";
import type { SurfaceModel } from "@a2ui/web_core/v0_9";
import { useSyncExternalStore, type CSSProperties } from "react";

import {
  getA2UIThemeRegistryVersion,
  resolveA2UIThemeTokens,
  subscribeA2UIThemeExtensions,
} from "../theme/registry";

export interface ValuzA2UISurfaceProps {
  surface: SurfaceModel<ReactComponentImplementation>;
  className?: string;
  theme?: "light" | "dark";
}

export function ValuzA2UISurface({ surface, className, theme }: ValuzA2UISurfaceProps) {
  useSyncExternalStore(
    subscribeA2UIThemeExtensions,
    getA2UIThemeRegistryVersion,
    getA2UIThemeRegistryVersion,
  );
  const resolvedTheme = theme ?? "light";
  const themeStyle = resolveA2UIThemeTokens(resolvedTheme) as CSSProperties;
  return (
    <div
      className={["valuz-a2ui", className].filter(Boolean).join(" ")}
      data-catalog={surface.catalog.id}
      data-a2ui-theme="default"
      data-theme={resolvedTheme}
      style={themeStyle}
    >
      <A2uiSurface surface={surface} />
    </div>
  );
}
