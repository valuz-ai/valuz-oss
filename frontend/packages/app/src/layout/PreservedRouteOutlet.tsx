import { useRef, type ReactElement } from "react";
import { useLocation, useOutlet } from "react-router-dom";

import { outletTransitionKey } from "./outlet-key";

interface CachedPage {
  location: ReturnType<typeof useLocation>;
  outlet: ReactElement | null;
}

export interface PreservedRouteOutletProps {
  context?: unknown;
  /** The current route is a transient, full-content overlay. */
  overlay?: boolean;
}

/**
 * Project-layout route outlet with generic detail-overlay preservation.
 *
 * Keeping the previous outlet element in the same keyed DOM position lets
 * React retain the complete page instance while a detail route is open. That
 * includes component state, fetched rows, the AppShell scroller, and any
 * nested scroll containers. Routes opt in declaratively; source pages and
 * links need no special handling.
 */
export function PreservedRouteOutlet({
  context,
  overlay = false,
}: PreservedRouteOutletProps) {
  const location = useLocation();
  const outlet = useOutlet(context);
  const pageRef = useRef<CachedPage | null>(null);

  const cachedPage = overlay ? pageRef.current : null;
  const hasBackgroundPage = cachedPage !== null;

  if (!overlay) {
    pageRef.current = { location, outlet };
  }

  const pageLocation = cachedPage?.location ?? location;
  const pageOutlet = cachedPage?.outlet ?? outlet;

  return (
    <>
      <div
        key={outletTransitionKey(pageLocation.pathname)}
        className="h-full min-h-0 animate-page-enter"
        aria-hidden={hasBackgroundPage || undefined}
        // Prevent mouse, keyboard, and accessibility interaction with the
        // retained page while the full-content detail overlay is active.
        inert={hasBackgroundPage || undefined}
      >
        {pageOutlet}
      </div>
      {hasBackgroundPage ? (
        <div
          data-route-overlay="true"
          className="absolute inset-0 z-30 min-h-0 animate-page-enter"
        >
          {outlet}
        </div>
      ) : null}
    </>
  );
}
