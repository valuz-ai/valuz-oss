import { useCallback, useEffect, useState } from "react";
import { providersApi } from "../api/providers-api";
import { providerHasUsableCredentials } from "./use-composer-providers";

/**
 * Whether the user has at least one model channel that could actually run a
 * turn — an enabled provider with usable credentials (configured API key,
 * linked OAuth account, or a logged-in subscription). Reuses the canonical
 * {@link providerHasUsableCredentials} rule so this matches the Composer model
 * picker and the Settings → Providers list exactly.
 *
 * Powers the conversation setup banner (10-new-conversation-guidance): a user
 * who skipped onboarding can land with no usable channel, and the banner nudges
 * them into setup instead of letting the first send dead-end.
 *
 * ``loaded`` gates the banner so it doesn't flash before the first fetch
 * resolves — and only a RESOLVED fetch counts. A failed request is not
 * knowledge: treating it as "loaded, no channel" made the banner claim "no
 * model configured" whenever ``/v1/providers`` merely errored or the backend
 * was briefly degraded, which is wrong advice (the user may have a perfectly
 * configured channel). On failure the hook keeps the banner gated and retries
 * until an actual answer arrives.
 *
 * ``refresh`` re-asks on demand. A managed install (capability
 * ``managedModelChannels``) gets its channels from a catalog it does not
 * control, so "none yet" there is usually "not delivered yet" — the banner
 * offers a retry rather than sending the user to a setup screen.
 */
export const USABLE_CHANNEL_RETRY_MS = 5_000;

export function useHasUsableChannel(): {
  hasChannel: boolean;
  loaded: boolean;
  refresh: () => void;
} {
  const [hasChannel, setHasChannel] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const attempt = () => {
      providersApi
        .list()
        .then(({ providers }) => {
          if (cancelled) return;
          setHasChannel(
            providers.some((p) => p.enabled && providerHasUsableCredentials(p)),
          );
          setLoaded(true);
        })
        .catch(() => {
          if (cancelled) return;
          timer = setTimeout(attempt, USABLE_CHANNEL_RETRY_MS);
        });
    };
    attempt();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [reload]);

  return { hasChannel, loaded, refresh };
}
