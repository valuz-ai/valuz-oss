import type { CSSProperties, FC } from "react";

/**
 * A transparent strip pinned to the top of the window that acts as a drag
 * handle for the frameless Electron window (``titleBarStyle: "hidden"``).
 *
 * Full-screen screens that don't render the project ``TopBar`` (the startup
 * splash, onboarding, the api-key screen) otherwise have no draggable region,
 * so the window can't be moved while they're shown. ``h-8`` clears the macOS
 * traffic lights, which remain clickable above the drag region. Inert on
 * web/headless (``-webkit-app-region`` is a no-op there).
 *
 * Place it once anywhere within a full-screen page — it is ``fixed`` to the
 * viewport, so its position in the tree doesn't matter.
 */
export const WindowDragRegion: FC<{ className?: string }> = ({ className }) => (
  <div
    aria-hidden
    className={`fixed inset-x-0 top-0 z-50 h-8 ${className ?? ""}`}
    style={{ WebkitAppRegion: "drag" } as CSSProperties}
  />
);
