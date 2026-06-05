import { useEffect } from "react";
import { useChatStore } from "../store/chat-store";

/**
 * Bind the chat store to a session id for the lifetime of the calling
 * component. Handles attach on mount / id change and detach on unmount.
 *
 * Pass ``null`` to leave the chat store unattached (e.g. when no session
 * is selected yet).
 */
export const useChatSession = (sessionId: string | null) => {
  const attach = useChatStore((s) => s.attach);
  const detach = useChatStore((s) => s.detach);

  useEffect(() => {
    if (!sessionId) {
      detach();
      return;
    }
    void attach(sessionId);
    return () => {
      // Detach when component unmounts or session id changes. The next
      // attach() will tear down the previous controller anyway, but
      // explicit detach keeps store state clean across navigations.
      detach();
    };
  }, [sessionId, attach, detach]);

  return useChatStore();
};
