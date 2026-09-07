import { Loader2Icon } from "lucide-react"

import { cn } from "@valuz/ui/lib/utils"

/**
 * Inline loading indicator (design-system tier 3): 14px, follows the text
 * colour, always sits next to the text it describes — so it is decorative
 * (`aria-hidden`) and never changes a button's accessible name. Page and
 * section loading states use `LoadingState` / `PageLoader` instead.
 */
function Spinner({ className, ...props }: React.ComponentProps<"svg">) {
  return (
    <Loader2Icon
      aria-hidden="true"
      className={cn("size-3.5 shrink-0 animate-spin", className)}
      {...props}
    />
  )
}

export { Spinner }
