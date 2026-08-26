import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { NEW_SESSION_ID } from "./session-events";

export type ConversationViewVariant = "page" | "panel";

type RoutingParams = {
  /** Controlled session id. ``undefined``/absent means "no session yet" —
   *  normalized to ``NEW_SESSION_ID`` internally, same sentinel the page
   *  route used via ``useParams``'s default. */
  sessionId: string | undefined;
  variant: ConversationViewVariant;
  /** ``panel`` variant only — invoked the moment a draft session is minted
   *  into a real one, so the embedding host can persist the id (finance:
   *  ``localStorage`` keyed by host). Unused for ``page`` (real navigation
   *  covers persistence via the URL). */
  onSessionCreated?: (sessionId: string) => void;
};

/**
 * ── Session-id routing bridge ────────────────────────────────────────
 *
 * ``ConversationPage`` used to read its session id straight off
 * ``useParams`` and let ``navigate()`` (called by ``useConversationSend``
 * at the moment a draft is minted into a real session) drive every
 * subsequent render via the route. That mechanism only exists where there
 * IS a route — a 345px embedded panel has none, so this hook generalizes
 * it: ``page`` variant reproduces the original id-tracking effect
 * (``previousRouteSessionIdRef`` / ``promotingSessionIdRef`` /
 * ``conversationInstanceKey``) verbatim off the ``sessionId`` prop instead
 * of ``useParams`` directly (the page shell still owns the actual
 * ``useParams()`` call and threads the result in); ``panel`` variant skips
 * the real navigation and instead updates its own ``id`` state directly,
 * informing the host through ``onSessionCreated``.
 */
export function useConversationRouting({
  sessionId,
  variant,
  onSessionCreated,
}: RoutingParams) {
  const navigate = useNavigate();
  const propId = sessionId ?? NEW_SESSION_ID;
  const previousPropIdRef = useRef(propId);
  // Set by ``useConversationSend`` right before it promotes a draft session
  // (mirrors the original ``promotingSessionIdRef`` in ``ConversationPage``)
  // — read by ``useConversationHistory``'s bootstrap fast-path too, so it
  // must be the SAME ref object threaded into both hooks.
  const promotingSessionIdRef = useRef<string | null>(null);
  const [internalId, setInternalId] = useState(propId);
  const [conversationInstanceKey, setConversationInstanceKey] = useState(
    () => `conversation:${propId}`,
  );

  useEffect(() => {
    const previousProp = previousPropIdRef.current;
    previousPropIdRef.current = propId;
    if (previousProp === propId) return;
    const isPromote =
      previousProp === NEW_SESSION_ID &&
      promotingSessionIdRef.current === propId;
    if (isPromote) {
      promotingSessionIdRef.current = null;
    } else {
      setConversationInstanceKey(`conversation:${propId}`);
    }
    setInternalId(propId);
  }, [propId]);

  const onSessionPromoted = useCallback(
    (newId: string, opts?: { skillCreator?: boolean }) => {
      promotingSessionIdRef.current = newId;
      if (variant === "page") {
        navigate(
          `/conversation/${newId}${opts?.skillCreator ? "?mode=skill-creator" : ""}`,
          {
            replace: true,
            state: { promotedFromNew: true, promotedSessionId: newId },
          },
        );
      } else {
        setInternalId(newId);
        onSessionCreated?.(newId);
      }
    },
    [variant, navigate, onSessionCreated],
  );

  return {
    // ``page`` mirrors the original component exactly — ``id`` IS the routed
    // prop. ``panel`` has no route to reflect a promotion back through
    // props, so it reads its own directly-updated state instead.
    id: variant === "panel" ? internalId : propId,
    conversationInstanceKey,
    promotingSessionIdRef,
    onSessionPromoted,
  };
}
