import type { CSSProperties } from "react";
import { ProjectLayoutBase } from "@valuz/app/layout";

export type { ProjectOutletContext } from "@valuz/app/layout";
export { useProjectOutlet } from "@valuz/app/layout";

const logoMenuContentStyle = {
  WebkitAppRegion: "no-drag",
} as CSSProperties;

export const DesktopProjectLayout = () => (
  <ProjectLayoutBase
    logoSrc="./logo.png"
    logoMenuContentStyle={logoMenuContentStyle}
    directoryFieldMode="picker"
  />
);
