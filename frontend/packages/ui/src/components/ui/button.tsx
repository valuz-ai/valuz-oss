import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { Slot } from "radix-ui";

import { cn } from "@valuz/ui/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive:
          "bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40",
        outline:
          "border border-surface-border bg-surface text-ink-label shadow-xs hover:bg-surface-2 hover:text-ink-heading dark:border-surface-border-strong dark:bg-surface-soft dark:hover:bg-surface-2 dark:hover:text-ink-heading",
        secondary:
          "border border-transparent bg-secondary text-secondary-foreground hover:bg-surface-muted hover:text-ink-heading dark:border-surface-border dark:bg-surface-soft dark:text-ink-label dark:hover:border-surface-border-strong dark:hover:bg-surface-2 dark:hover:text-ink-heading",
        ghost:
          "hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
        "icon-xs": "size-6 rounded-md [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-8",
        "icon-lg": "size-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  loading = false,
  disabled,
  children,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
    /** When true, shows a spinner and disables the button */
    loading?: boolean;
  }) {
  const Comp = asChild ? Slot.Root : "button";

  // When ``asChild`` is set, ``Slot.Root`` requires exactly ONE React
  // element child (radix calls ``React.Children.only`` internally).
  // Any extra sibling — including a falsy ``{loading && <Loader2/>}``
  // expression — turns the children prop into an array and crashes
  // with "React.Children.only expected to receive a single React
  // element child" the moment the wrapping component renders. So in
  // asChild mode we hand off the user's child verbatim and skip the
  // spinner overlay; callers that need a loading state should use
  // ``<Button asChild={false}>`` instead.
  const content = asChild ? (
    children
  ) : (
    <>
      {loading && (
        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin [&]:pointer-events-none [&]:shrink-0" />
      )}
      {children}
    </>
  );

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      disabled={disabled || loading}
      {...props}
    >
      {content}
    </Comp>
  );
}

export { Button, buttonVariants };
