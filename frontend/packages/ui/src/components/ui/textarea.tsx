import * as React from "react";

import { cn } from "@valuz/ui/lib/utils";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      // ``field-sizing-content`` makes the textarea auto-grow with its
      // content — great for short prompts, disastrous for thousand-line
      // pastes (was blowing out the agent edit dialog past the viewport).
      // ``max-h-[40vh]`` caps the visible height so the textarea grows
      // until ~40% of the viewport, then its own scrollbar takes over.
      className={cn(
        "flex field-sizing-content max-h-[40vh] min-h-16 w-full overflow-y-auto rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:aria-invalid:ring-destructive/40",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
