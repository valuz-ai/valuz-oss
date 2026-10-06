import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "@valuz/core";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@valuz/ui";

import { thirdPartyBridge } from "./services";

interface PendingConfirm {
  title: string;
  body?: string;
  confirmLabel?: string;
  resolve: (confirmed: boolean) => void;
}

/**
 * Mounted in ``shell.overlay`` while third-party plugins are loaded. It hands
 * the plugin SDK the router's ``navigate`` and a host-styled confirm dialog
 * (``host.navigate`` / ``host.confirm``); renders nothing otherwise.
 */
export function ThirdPartyBridge() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const pendingRef = useRef<PendingConfirm | null>(null);

  useEffect(() => {
    thirdPartyBridge.navigate = (path, options) => {
      void navigate(path, { state: options?.state, replace: options?.replace });
    };
    thirdPartyBridge.confirm = (options) =>
      new Promise<boolean>((resolve) => {
        // A second dialog while one is open: the first is answered "no".
        pendingRef.current?.resolve(false);
        const next = { ...options, resolve };
        pendingRef.current = next;
        setPending(next);
      });
    return () => {
      thirdPartyBridge.navigate = null;
      thirdPartyBridge.confirm = null;
      pendingRef.current?.resolve(false);
      pendingRef.current = null;
    };
  }, [navigate]);

  const answer = useCallback((confirmed: boolean) => {
    const current = pendingRef.current;
    pendingRef.current = null;
    setPending(null);
    current?.resolve(confirmed);
  }, []);

  if (!pending) return null;
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open) answer(false);
      }}
    >
      <AlertDialogContent
        // Radix wants a description; with no body there is nothing to describe.
        {...(pending.body ? {} : { "aria-describedby": undefined })}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>{pending.title}</AlertDialogTitle>
          {pending.body ? (
            <AlertDialogDescription className="break-words">
              {pending.body}
            </AlertDialogDescription>
          ) : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => answer(false)}>
            {t("common.cancel")}
          </AlertDialogCancel>
          <AlertDialogAction onClick={() => answer(true)}>
            {pending.confirmLabel ?? t("common.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
