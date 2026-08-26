import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@valuz/ui/lib/utils"

const badgeVariants = cva(
  "inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-sm border border-transparent px-2 py-0 text-2xs font-medium whitespace-nowrap transition-[color,box-shadow] focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-invalid:border-destructive aria-invalid:ring-destructive/20 [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground [a&]:hover:bg-primary-hover",
        secondary:
          "border-border bg-transparent text-ink-body [a&]:hover:bg-surface-2 [a&]:hover:text-ink-heading",
        destructive:
          "bg-destructive text-white focus-visible:ring-destructive [a&]:hover:bg-error-hover",
        outline:
          "border-border text-ink-body [a&]:hover:bg-surface-2 [a&]:hover:text-ink-heading",
        ghost: "[a&]:hover:bg-surface-2 [a&]:hover:text-ink-heading",
        link: "text-primary underline-offset-4 [a&]:hover:underline",
        brand: "bg-info-light text-info-text [a&]:hover:bg-brand-100",
        metaBrand:
          "bg-brand-100 text-brand-700 [a&]:hover:bg-brand-200",
        metaOutline:
          "border-border bg-transparent text-ink-meta [a&]:hover:bg-surface-soft [a&]:hover:text-ink-heading",
        metaNeutral:
          "bg-surface-soft text-ink-body [a&]:hover:bg-surface-muted",
        success:
          "bg-success-light text-success-text border-transparent",
        warning: "bg-warning-light text-warning-text border-transparent",
        error: "bg-error-light text-error-text border-transparent",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
