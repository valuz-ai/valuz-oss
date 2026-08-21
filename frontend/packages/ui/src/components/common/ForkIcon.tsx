import type { ComponentProps } from "react";
import { Split } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * The session-fork glyph (docs/design/session-fork.md): lucide ``Split``
 * rotated 90° so the flow reads left-to-right before branching. One
 * component so every fork affordance (header menu, message hover, sidebar
 * and activity row menus, forked-from chip) shares the exact glyph —
 * chosen over ``GitFork`` (dot-network, collides with the share icon) and
 * ``GitBranch`` (taken by the worktree badge).
 */
export const ForkIcon = ({
  className,
  ...props
}: ComponentProps<typeof Split>) => (
  <Split className={cn("rotate-90", className)} {...props} />
);
