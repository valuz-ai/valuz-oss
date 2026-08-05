import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { Dispatch, SetStateAction } from "react";
import type { ConversationTurn } from "@valuz/shared";
import type { SessionEventDTO } from "@valuz/core";

type ConversationScrollParams = {
  selectedSessionId: string | null;
  events: SessionEventDTO[];
  effectiveTurns: ConversationTurn[];
  /** Dep-only: its identity re-arms the pinned-turn layout effect. */
  pendingUserMessage: unknown;
  hasMoreOlder: boolean;
  loadOlderTurns: () => Promise<void>;
  scrollContainerRef: { current: HTMLDivElement | null };
  topSentinelRef: { current: HTMLButtonElement | null };
  userScrolledRef: { current: boolean };
  pendingScrollAnchorRef: {
    current: { oldScrollHeight: number; oldScrollTop: number } | null;
  };
  pinNextTurnToTopRef: { current: boolean };
  keepCurrentTurnAtTopRef: { current: boolean };
  setSending: Dispatch<SetStateAction<boolean>>;
};

/**
 * ── Conversation scroll / virtualization ─────────────────────────────
 *
 * Owns the scroll cluster of the conversation page: the scroll-to-bottom
 * affordance + handler, the virtual-list API handoff, the send-time
 * pin-to-top and continuous turn anchoring, the upward pager's scroll
 * restoration + top-sentinel observer wiring, streaming follow, the
 * entry scroll, and the session-switch ``sending`` release. Bodies are
 * moved verbatim from ``ConversationPage``.
 */
export function useConversationScroll({
  selectedSessionId,
  events,
  effectiveTurns,
  pendingUserMessage,
  hasMoreOlder,
  loadOlderTurns,
  scrollContainerRef,
  topSentinelRef,
  userScrolledRef,
  pendingScrollAnchorRef,
  pinNextTurnToTopRef,
  keepCurrentTurnAtTopRef,
  setSending,
}: ConversationScrollParams) {
  // Moved-in declarations (previously page-level): only this cluster
  // touches them; the page JSX consumes the returned values instead.
  const turnListVirtualApiRef = useRef<{
    scrollToTurnTop: (index: number) => void;
  } | null>(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);

  // Scroll to bottom
  const handleScrollToBottom = useCallback(() => {
    keepCurrentTurnAtTopRef.current = false;
    scrollContainerRef.current?.scrollTo({
      top: scrollContainerRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, []);

  const handleTurnListVirtualApiReady = useCallback(
    (api: { scrollToTurnTop: (index: number) => void } | null) => {
      turnListVirtualApiRef.current = api;
    },
    [],
  );

  // Show the scroll-to-bottom affordance whenever there's overflow not
  // currently in view — either because the user scrolled up (scroll
  // listener) or because the content grew past the viewport without any
  // user interaction (streaming output, new turns). The ResizeObserver
  // fires on the scroll container AND its inner content so we catch
  // both ``clientHeight`` shrinks and ``scrollHeight`` growths; without
  // observing the inner element, streaming text inflates scrollHeight
  // silently and the button only appears after the user nudges the
  // scroll position.
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const recompute = () => {
      const distanceFromBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight;
      setShowScrollBottom((prev) => {
        const next = distanceFromBottom > 120;
        return prev === next ? prev : next;
      });
    };
    recompute();
    el.addEventListener("scroll", recompute, { passive: true });
    // ResizeObserver watches the scroll container + first-child chain so
    // size growth from streaming text propagates here. The structure is:
    //   el (overflow-y-auto)
    //   └─ ConversationTurnList outer (mx-auto max-w-[760px] px-6)
    //      └─ virtualizer wrapper (style.height = totalSize)
    //         └─ virtual rows (absolutely positioned, don't affect flow)
    // Walk a few levels deep so the virtualizer-wrapper's inline-style
    // height change triggers recompute too.
    const ro = new ResizeObserver(recompute);
    let cursor: Element | null = el;
    for (let depth = 0; cursor && depth < 5; depth += 1) {
      ro.observe(cursor);
      cursor = cursor.firstElementChild;
    }
    // MutationObserver as a second source: turn-level fold/unfold and
    // segment expand/collapse add or remove DOM nodes inside virtual
    // rows. Those structural changes don't always reshape the outer
    // flow-positioned containers cleanly (rows are absolutely
    // positioned), so ResizeObserver can miss them. ``childList`` +
    // ``subtree`` fires whenever a segment / SegmentDetails body is
    // mounted or unmounted; we coalesce successive mutations into one
    // RAF tick to keep the work cheap during fast batches.
    let mutationScheduled = false;
    const scheduleMutationRecompute = () => {
      if (mutationScheduled) return;
      mutationScheduled = true;
      requestAnimationFrame(() => {
        mutationScheduled = false;
        recompute();
      });
    };
    const mo = new MutationObserver(scheduleMutationRecompute);
    mo.observe(el, { subtree: true, childList: true });
    // 250ms polling as a robustness fallback. SegmentDetails owns its own
    // ``open`` state — toggling it doesn't bubble a re-render to this page,
    // and the virtualizer's totalSize update chain (measureElement → cache
    // → re-render → outer wrapper resize → our RO) is async with RAF
    // boundaries that don't always line up with our recompute scheduling.
    // A coarse interval check guarantees the button visibility eventually
    // reflects reality even when observers race the layout.
    const pollInterval = window.setInterval(recompute, 250);
    return () => {
      el.removeEventListener("scroll", recompute);
      ro.disconnect();
      mo.disconnect();
      window.clearInterval(pollInterval);
    };
  }, []);

  // Track the scroll container's clientHeight. We size the latest
  // turn's ``min-height`` to this so a follow-up Send can snap the
  // user's new message to the viewport top in one commit — without it
  // ``scrollHeight - clientHeight`` clamps ``scrollTop`` and the
  // browser pins the new turn to the bottom instead. Re-measured via
  // ResizeObserver so window resizes and right-panel toggles keep the
  // layout consistent.
  const [containerHeight, setContainerHeight] = useState(0);
  useLayoutEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    setContainerHeight(el.clientHeight);
    const ro = new ResizeObserver(() => {
      setContainerHeight((prev) =>
        Math.abs(prev - el.clientHeight) < 4 ? prev : el.clientHeight,
      );
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (!pinNextTurnToTopRef.current) return;
    if (effectiveTurns.length > 0) {
      turnListVirtualApiRef.current?.scrollToTurnTop(effectiveTurns.length - 1);
    }
    pinNextTurnToTopRef.current = false;
    setShowScrollBottom(false);
  }, [effectiveTurns.length, pendingUserMessage, containerHeight]);

  // Continuous scroll anchor for the latest turn while
  // ``keepCurrentTurnAtTopRef`` is active. The one-shot
  // ``scrollToTurnTop`` above pins the new turn at viewport top in the
  // first 8 frames after send, but layout settles asynchronously over
  // a much longer window:
  //   - markdown image/table inside an earlier turn finalising layout
  //     (RO fires seconds after the row mounted)
  //   - virtualizer measurement-cache key swap when the optimistic
  //     ``pending-turn`` is replaced by ``turn-X`` after ``message.user``
  //     echoes back
  //   - tail-spacer height recompute coupled with virtualizer wrapper
  //     resize on streaming content growth
  // Any of those can shift the latest turn's docY by tens or hundreds
  // of pixels; the user perceives "empty space above the new turn that
  // keeps growing during streaming".
  //
  // Strategy: capture the latest turn's docY at send time, then on
  // every layout change re-read it and shift ``scrollTop`` by the
  // delta so the docY ↔ viewport-top relationship stays fixed. Stops
  // on user-initiated scroll (wheel / touch / keyboard) so the
  // anchor doesn't fight the user's intent.
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    let lastTargetDocY: number | null = null;
    let anchoredIdx = -1;

    const release = () => {
      keepCurrentTurnAtTopRef.current = false;
      lastTargetDocY = null;
      anchoredIdx = -1;
    };

    const adjust = () => {
      if (!keepCurrentTurnAtTopRef.current) {
        lastTargetDocY = null;
        anchoredIdx = -1;
        return;
      }
      const lastIdx = effectiveTurnsRef.current.length - 1;
      if (lastIdx < 0) return;
      // A new send appends a turn → ``lastIdx`` advances. The OLD
      // baseline (``lastTargetDocY`` captured against the previous
      // latest turn) is meaningless against the NEW target — using
      // it would compute a delta in the hundreds and shove
      // ``scrollTop`` back where the new turn should NOT be. Re-arm
      // the baseline so the next adjust call captures the new
      // target's docY (which by then ``scrollToTurnTop`` has already
      // pinned to viewport top).
      if (lastIdx !== anchoredIdx) {
        anchoredIdx = lastIdx;
        lastTargetDocY = null;
      }
      const target = container.querySelector(
        `[data-index="${lastIdx}"]`,
      ) as HTMLElement | null;
      if (!target) return;
      const containerRect = container.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const docY = container.scrollTop + (targetRect.top - containerRect.top);
      if (lastTargetDocY === null) {
        lastTargetDocY = docY;
        return;
      }
      const delta = docY - lastTargetDocY;
      if (Math.abs(delta) < 1) return;
      container.scrollTop += delta;
      // After the scroll commits the target is back at the anchored
      // viewport position, so the ``docY`` we just observed becomes the
      // new reference point.
      lastTargetDocY = docY;
    };

    const ro = new ResizeObserver(adjust);
    let cursor: Element | null = container;
    for (let depth = 0; cursor && depth < 5; depth += 1) {
      ro.observe(cursor);
      cursor = cursor.firstElementChild;
    }
    let mutationScheduled = false;
    const scheduleMutationAdjust = () => {
      if (mutationScheduled) return;
      mutationScheduled = true;
      requestAnimationFrame(() => {
        mutationScheduled = false;
        adjust();
      });
    };
    const mo = new MutationObserver(scheduleMutationAdjust);
    mo.observe(container, { subtree: true, childList: true });

    // User-initiated scroll cancels the anchor. Distinguish from our
    // own programmatic ``scrollTop +=`` writes by gating on the
    // physical input events rather than the ``scroll`` event itself.
    container.addEventListener("wheel", release, { passive: true });
    container.addEventListener("touchstart", release, { passive: true });
    const onKey = (e: KeyboardEvent) => {
      if (
        e.key === "PageDown" ||
        e.key === "PageUp" ||
        e.key === "ArrowUp" ||
        e.key === "ArrowDown" ||
        e.key === "Home" ||
        e.key === "End"
      ) {
        release();
      }
    };
    container.addEventListener("keydown", onKey);

    return () => {
      ro.disconnect();
      mo.disconnect();
      container.removeEventListener("wheel", release);
      container.removeEventListener("touchstart", release);
      container.removeEventListener("keydown", onKey);
    };
  }, []);

  // Mirror ``effectiveTurns`` into a ref so the anchor effect (set up
  // once on mount) can read the live last-index without re-subscribing.
  const effectiveTurnsRef = useRef(effectiveTurns);
  useEffect(() => {
    effectiveTurnsRef.current = effectiveTurns;
  }, [effectiveTurns]);

  // Restore scroll position after the upward pager prepended events.
  // Without this the browser keeps ``scrollTop`` constant while the new
  // content pushes existing items downward — visually the user sees a
  // sudden jump and loses their place.
  //
  // ``pendingScrollAnchorRef`` is set inside ``loadOlderTurns`` right
  // before the ``setEvents`` that prepends. After React commits and
  // reflows, ``scrollHeight`` reflects the new total height; we add the
  // delta to ``scrollTop`` to keep the previously-visible row in the
  // same screen position. Guard on the ref so this no-ops for SSE
  // appends and other ``events`` updates.
  useLayoutEffect(() => {
    const anchor = pendingScrollAnchorRef.current;
    if (!anchor) return;
    const el = scrollContainerRef.current;
    if (el) {
      const delta = el.scrollHeight - anchor.oldScrollHeight;
      el.scrollTop = anchor.oldScrollTop + delta;
    }
    pendingScrollAnchorRef.current = null;
  }, [events.length]);

  // First-real-scroll detector. Listens for ``wheel`` and ``keydown``
  // events on the scroll container — these fire only on user-initiated
  // scrolls. Native ``scroll`` events are unreliable here because both
  // programmatic scrolling (auto-scroll-to-bottom on initial load,
  // scroll-anchor restoration after prepend) and ResizeObserver
  // re-measurements emit them, which would falsely flip the flag during
  // the initial-mount race we're guarding against.
  //
  // Re-attached when the session changes so a freshly-loaded session
  // starts back at "needs first real scroll".
  useEffect(() => {
    userScrolledRef.current = false;
    const el = scrollContainerRef.current;
    if (!el) return;
    const handler = () => {
      userScrolledRef.current = true;
    };
    el.addEventListener("wheel", handler, { passive: true });
    el.addEventListener("keydown", handler);
    el.addEventListener("touchmove", handler, { passive: true });
    return () => {
      el.removeEventListener("wheel", handler);
      el.removeEventListener("keydown", handler);
      el.removeEventListener("touchmove", handler);
    };
  }, [selectedSessionId]);

  // Top sentinel observer — when it enters the scroll viewport (rootMargin
  // pulls the trigger down by 200 px so we start fetching just before
  // the user actually hits the top), kick off the next page of older
  // turns.
  //
  // ``hasMoreOlder`` is in the deps because the sentinel JSX is gated on
  // it: on first mount the sentinel isn't in the DOM yet (initial load
  // is in flight), so ``topSentinelRef.current`` is null and the effect
  // early-returns. Once the API resolves and ``hasMoreOlder`` flips
  // true the sentinel renders, the effect re-runs, and the observer
  // finally attaches. Without this dep the observer would never bind to
  // the post-load sentinel and pagination would silently never fire.
  useEffect(() => {
    const sentinel = topSentinelRef.current;
    const scroller = scrollContainerRef.current;
    if (!sentinel || !scroller) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          void loadOlderTurns();
        }
      },
      {
        root: scroller,
        rootMargin: "200px 0px 0px 0px",
        threshold: 0,
      },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadOlderTurns, hasMoreOlder]);

  // Auto-scroll during streaming — debounced + rAF batched so a burst of
  // SSE deltas (one per token, often 30+/sec from Claude) coalesces into
  // at most one scroll per 120ms. Use an instant jump for automatic
  // following: historical DB catch-up can append several events at once,
  // and smooth scrolling those updates makes the page visibly replay the
  // whole transcript before landing at the bottom.
  //
  // The two-RAF wait is preserved so the scroll waits for React to flush
  // the new blocks AND the browser to measure ``scrollHeight`` — without
  // it the very first historical-events batch can pin the viewport at
  // the top instead of the latest message.
  const scrollSettleTimerRef = useRef<number | null>(null);
  const scrollLastFiredRef = useRef(0);
  useEffect(() => {
    if (
      !scrollContainerRef.current ||
      showScrollBottom ||
      keepCurrentTurnAtTopRef.current
    )
      return;
    const el = scrollContainerRef.current;

    const fire = () => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          el.scrollTop = el.scrollHeight;
          scrollLastFiredRef.current = performance.now();
        });
      });
    };

    const elapsed = performance.now() - scrollLastFiredRef.current;
    const SCROLL_DEBOUNCE_MS = 120;

    if (elapsed >= SCROLL_DEBOUNCE_MS) {
      fire();
    } else if (scrollSettleTimerRef.current === null) {
      scrollSettleTimerRef.current = window.setTimeout(() => {
        scrollSettleTimerRef.current = null;
        fire();
      }, SCROLL_DEBOUNCE_MS - elapsed);
    }

    return () => {
      if (scrollSettleTimerRef.current !== null) {
        window.clearTimeout(scrollSettleTimerRef.current);
        scrollSettleTimerRef.current = null;
      }
    };
  }, [events, showScrollBottom]);

  // Initial mount / session switch: jump to bottom once the conversation
  // finishes loading the historical replay, even if ``events`` was already
  // populated before the container mounted.
  useEffect(() => {
    pinNextTurnToTopRef.current = false;
    keepCurrentTurnAtTopRef.current = false;
    if (!scrollContainerRef.current) return;
    const el = scrollContainerRef.current;
    const r = requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight });
    });
    return () => cancelAnimationFrame(r);
  }, [selectedSessionId]);

  // Entering a conversation that's already RUNNING: land on the live bottom so
  // the user sees the streaming output, not the top. The generic entry scroll
  // above fires a single rAF on session change, but a running session is still
  // replaying history / streaming, so its content settles over several frames —
  // and once it paints, ``showScrollBottom`` can latch true (first paint at the
  // top), which blocks the events-follow effect and strands the viewport up top.
  // A short burst of scroll-to-bottom + latch-clear across the load window pins
  // it to the newest message; the normal follow takes over afterwards.
  //
  // Only the FIRST status observation after entering a session counts (tracked
  // per session id): if it's already running, jump; otherwise leave scrolling to
  // the generic entry effect + the send-time pin-to-top, so a later idle→running
  // from the user's OWN send isn't yanked to the bottom.
  // Open every conversation on its newest message — running or ended alike. The
  // generic entry scroll above fires a single rAF on session change, but the
  // transcript settles over later frames (history replay / streaming), and once
  // it paints ``showScrollBottom`` can latch true (first paint at the top),
  // blocking the events-follow effect and stranding the viewport up top. Burst
  // scroll-to-bottom + latch-clear across the settle window guarantees the
  // bottom. Gated to the FIRST transcript load per session id via the ref, so a
  // later send / streaming delta (which also bumps ``events.length``) doesn't
  // re-trigger it and fight the send-time pin-to-top.
  const entryScrolledRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedSessionId) return;
    if (entryScrolledRef.current === selectedSessionId) return;
    if (events.length === 0) return; // wait for the transcript window to load
    entryScrolledRef.current = selectedSessionId;
    if (!scrollContainerRef.current) return;
    let cancelled = false;
    const jump = () => {
      const node = scrollContainerRef.current;
      if (cancelled || !node) return;
      // The burst exists to survive the multi-frame settle of the initial
      // transcript paint — not to fight the user. Once a real scroll gesture
      // has landed (wheel/keydown/touchmove; the ref resets on session
      // switch), the user owns the viewport: a late timer (up to 1s) yanking
      // them back to the bottom reads as the page "snapping away" from the
      // history they just scrolled up to.
      if (userScrolledRef.current) return;
      node.scrollTop = node.scrollHeight;
      setShowScrollBottom(false);
    };
    const raf = requestAnimationFrame(jump);
    const timers = [120, 300, 600, 1000].map((ms) =>
      window.setTimeout(jump, ms),
    );
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      timers.forEach((tm) => window.clearTimeout(tm));
    };
  }, [selectedSessionId, events.length]);

  // Reset per-session optimistic state on a real session→session switch.
  // The session-open effect below supersedes the previous stream on its own
  // (``subscribeToSession`` aborts the prior controller), so the only job
  // left here is releasing ``sending`` — the new session must not inherit
  // the previous one's click→turn-start pending flag. The ``null → id``
  // transition (the ``/conversation/new`` promotion) is skipped: the pending
  // flag there belongs to the send that minted the session.
  const prevSelectedSessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevSelectedSessionIdRef.current;
    prevSelectedSessionIdRef.current = selectedSessionId;
    if (prev === null || prev === selectedSessionId) return;
    setSending(false);
  }, [selectedSessionId]);

  return {
    showScrollBottom,
    containerHeight,
    handleScrollToBottom,
    handleTurnListVirtualApiReady,
  };
}
