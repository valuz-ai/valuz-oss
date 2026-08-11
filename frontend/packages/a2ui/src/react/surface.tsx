import { A2uiSurface, type ReactComponentImplementation } from "@a2ui/react/v0_9";
import type { SurfaceModel } from "@a2ui/web_core/v0_9";

export interface ValuzA2UISurfaceProps {
  surface: SurfaceModel<ReactComponentImplementation>;
  className?: string;
  theme?: "light" | "dark";
}

export function ValuzA2UISurface({ surface, className, theme }: ValuzA2UISurfaceProps) {
  return (
    <div
      className={["valuz-a2ui", className].filter(Boolean).join(" ")}
      data-catalog={surface.catalog.id}
      data-theme={theme}
    >
      <A2uiSurface surface={surface} />
    </div>
  );
}
