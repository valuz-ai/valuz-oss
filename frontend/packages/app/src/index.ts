/**
 * @valuz/app — shared pages, platform context, and layout types.
 *
 * All exports are browser/renderer-only. No Node code in this package.
 */

export { PlatformProvider, usePlatform, WebPlatformProvider } from "./platform";
export type { ProjectOutletContext } from "./layout";
export { useProjectOutlet } from "./layout";
export { toast } from "sonner";
