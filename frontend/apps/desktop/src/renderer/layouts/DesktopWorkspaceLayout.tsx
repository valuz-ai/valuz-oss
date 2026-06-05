import type { CSSProperties } from "react";
import { WorkspaceLayoutBase } from "@valuz/app/layout";

export type { WorkspaceOutletContext } from "@valuz/app/layout";
export { useWorkspaceOutlet } from "@valuz/app/layout";

const logoMenuContentStyle = {
  WebkitAppRegion: "no-drag",
} as CSSProperties;

export const DesktopWorkspaceLayout = () => (
  <WorkspaceLayoutBase
    logoSrc="./logo.png"
    logoMenuContentStyle={logoMenuContentStyle}
    directoryFieldMode="picker"
  />
);
