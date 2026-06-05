import { useEffect, useState } from "react";
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
 * resolves.
 */
export function useHasUsableChannel(): {
  hasChannel: boolean;
  loaded: boolean;
} {
  const [hasChannel, setHasChannel] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    providersApi
      .list()
      .then(({ providers }) => {
        if (cancelled) return;
        setHasChannel(
          providers.some((p) => p.enabled && providerHasUsableCredentials(p)),
        );
      })
      .catch(() => {
        if (!cancelled) setHasChannel(false);
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { hasChannel, loaded };
}
