import { useCallback, useLayoutEffect, useRef } from "react";

import { dispatchGenUIAction, type GenUIActionEvent } from "./action-registry";

/** Keep surface callbacks stable while reading only the committed host identity. */
export function useActionForwarder(
  hostParams: Record<string, string | number | boolean> | undefined,
) {
  const hostParamsRef = useRef(hostParams);
  useLayoutEffect(() => {
    hostParamsRef.current = hostParams;
  }, [hostParams]);

  return useCallback((action: Omit<GenUIActionEvent, "host">) => {
    const host = hostParamsRef.current;
    dispatchGenUIAction({ ...action, ...(host ? { host } : {}) });
  }, []);
}
