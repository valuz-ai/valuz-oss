/**
 * No-op platform provider for webui / browser environments.
 * Delegates to the shared ``webCapabilities`` (also used as the implicit
 * fallback inside ``usePlatform()`` when no provider is mounted).
 */

import type { ReactNode } from "react";
import { PlatformProvider } from "./context";
import { webCapabilities } from "./web-capabilities";

export function WebPlatformProvider({ children }: { children: ReactNode }) {
  return (
    <PlatformProvider value={webCapabilities}>{children}</PlatformProvider>
  );
}
