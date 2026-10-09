import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import { Dialog } from "../ui/dialog";
import { DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "../ui/dialog";
import { Button } from "../ui/button";

export interface FormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  /** Form field area — wrapped in `div.space-y-4` */
  children: ReactNode;
  /** Custom footer. When provided, overrides the default Cancel/Submit row. */
  footer?: ReactNode;
  /** Submit handler. When set, a Submit button is rendered (unless `footer` overrides it). */
  onSubmit?: () => void;
  /** Label for the submit button. */
  submitLabel?: string;
  /** Label for the cancel button. */
  cancelLabel?: string;
  /** Shows spinner on submit and disables both buttons. */
  loading?: boolean;
  /** Use destructive variant for the submit button. */
  destructive?: boolean;
  /** Override dialog max-width class, e.g. "sm:max-w-xl". */
  maxWidthClass?: string;
  className?: string;
}

export const FormDialog = ({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  onSubmit,
  submitLabel = "Submit",
  cancelLabel = "Cancel",
  loading = false,
  destructive = false,
  maxWidthClass,
  className,
}: FormDialogProps) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent
      className={cn(maxWidthClass, className)}
      {...(description ? {} : { "aria-describedby": undefined })}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>

      <div className="space-y-4">{children}</div>

      {footer ?? (
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            {cancelLabel}
          </Button>
          {onSubmit && (
            <Button
              variant={destructive ? "destructive" : "default"}
              onClick={onSubmit}
              loading={loading}
            >
              {submitLabel}
            </Button>
          )}
        </DialogFooter>
      )}
    </DialogContent>
  </Dialog>
);
