/**
 * WorkspaceOutletContext — the interface pages use to communicate
 * with their parent layout (set right panel, header, etc.).
 *
 * Desktop's DesktopWorkspaceLayout provides this via <Outlet context={...}>.
 * Webui can provide its own simpler layout.
 */

import type { ReactNode } from "react";
import { useOutletContext } from "react-router-dom";

export interface WorkspaceOutletContext {
  setRightPanel: (node: ReactNode | null) => void;
  setHeader: (node: ReactNode | null) => void;
  setHeaderClassName: (cls: string | undefined) => void;
  setHideHeader: (hide: boolean) => void;
  setAsideClassName: (cls: string | undefined) => void;
  setMainClassName: (cls: string | undefined) => void;
  setContentInnerClassName: (cls: string | undefined) => void;
}

export function useWorkspaceOutlet(): WorkspaceOutletContext {
  return useOutletContext<WorkspaceOutletContext>();
}
