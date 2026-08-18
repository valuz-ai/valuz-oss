/**
 * Lucide glyph for an execution target / origin tag.
 *
 * ``local`` (this machine) and ``cloud`` used to be the only two kinds and
 * every unknown id fell back to the hard-drive glyph — which made a remote
 * desktop (``device:<id>``) look exactly like "local". The kind now comes
 * from ``executionTargetIconKind`` (explicit ``icon`` on the registered
 * target, else inferred from the id) so each kind gets its own picture.
 */

import {
  executionTargetIconKind,
  type ExecutionTarget,
  type ExecutionTargetIcon,
} from "@valuz/core";
import { Cloud, HardDrive, Monitor, type LucideIcon } from "lucide-react";

const ICONS: Record<ExecutionTargetIcon, LucideIcon> = {
  local: HardDrive,
  cloud: Cloud,
  device: Monitor,
};

export function executionTargetIcon(
  targetId: string,
  target?: ExecutionTarget | null,
): LucideIcon {
  return ICONS[executionTargetIconKind(targetId, target)];
}
