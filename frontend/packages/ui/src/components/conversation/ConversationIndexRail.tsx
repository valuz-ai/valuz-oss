import type { MouseEvent as ReactMouseEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConversationTurn } from "@valuz/shared";
import {
  buildSegments,
  summarizeSegmentPhrase,
  turnPreviewText,
} from "@valuz/shared";
import { useI18n } from "../../hooks/use-i18n";
import { cn } from "../../lib/cn";
import { formatTurnTime } from "./turn-time";

/** Below this many turns the transcript is short enough to scan by
 * scrolling — a rail would be chrome with nothing to navigate. */
const MIN_TURNS_FOR_RAIL = 5;

/** The transcript column is ``max-w-[760px]`` and centred. Narrower than
 * this and the gutter the rail lives in is gone, so it would sit on top of
 * the messages instead of beside them — right panel open, artifact pane
 * split, or just a small window. Hide rather than overlap. */
const MIN_CONTAINER_WIDTH = 860;

/** How much of the user message the hover card shows. */
const PREVIEW_CHARS = 140;

/** Dock-style magnification. The tick under the cursor extends to
 * ``TICK_MAX_W`` and its neighbours taper back to ``TICK_BASE_W`` over
 * ``MAGNIFY_RADIUS`` ticks, so the rail reads as one accordion opening
 * around the cursor instead of a single tick popping out. */
const TICK_BASE_W = 10;
const TICK_ACTIVE_W = 16;
const TICK_MAX_W = 26;
const MAGNIFY_RADIUS = 3;

/**
 * Raised-cosine falloff. Smooth at both ends — no kink under the cursor,
 * no step where magnification stops — which is what makes the column read
 * as one elastic band rather than a set of independent bars.
 *
 * ``pointer`` is fractional: it tracks the cursor BETWEEN ticks, so the
 * widths interpolate continuously as the mouse travels instead of
 * snapping from one tick's curve to the next.
 */
const tickWidth = (
  index: number,
  pointer: number | null,
  activeIndex: number,
): number => {
  if (pointer === null) {
    return index === activeIndex ? TICK_ACTIVE_W : TICK_BASE_W;
  }
  const distance = Math.abs(index - pointer);
  if (distance > MAGNIFY_RADIUS) return TICK_BASE_W;
  const falloff =
    (Math.cos((distance / (MAGNIFY_RADIUS + 1)) * Math.PI) + 1) / 2;
  return TICK_BASE_W + (TICK_MAX_W - TICK_BASE_W) * falloff;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

type Hover = {
  /** Fractional cursor position along the rail — drives the widths. */
  pointer: number;
  /** Nearest whole tick — drives the card and the highlight. */
  index: number;
  /** That tick's centre, relative to the rail root. */
  anchorTop: number;
};

type ConversationIndexRailProps = {
  turns: ConversationTurn[];
  /** Index of the turn currently at the top of the viewport. */
  activeIndex: number;
  onSelect: (index: number) => void;
};

/**
 * ── Message index rail ───────────────────────────────────────────────
 *
 * A tick per user message, pinned to the left edge of the transcript.
 * Hovering pops that turn's gist to the right (prompt preview + what the
 * assistant did); clicking scrolls the turn to the top of the viewport.
 * The virtualized transcript gives the native scrollbar no sense of turn
 * boundaries, so this is the only way to see the shape of a long
 * conversation at a glance.
 *
 * The whole rail is one hover card, not one per tick: a 200-turn session
 * would otherwise mount 200 Radix portals for a card only ever shown one
 * at a time.
 */
export function ConversationIndexRail({
  turns,
  activeIndex,
  onSelect,
}: ConversationIndexRailProps) {
  const { t } = useI18n();
  const rootRef = useRef<HTMLElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const tickRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [hover, setHover] = useState<Hover | null>(null);
  const [hasRoom, setHasRoom] = useState(true);

  // Width guard — measured rather than expressed as a media query because
  // the space the rail needs depends on the right panel and the artifact
  // split pane, not on the window.
  useEffect(() => {
    const parent = rootRef.current?.parentElement;
    if (!parent) return;
    const recompute = () =>
      setHasRoom((prev) => {
        const next = parent.clientWidth >= MIN_CONTAINER_WIDTH;
        return prev === next ? prev : next;
      });
    recompute();
    const ro = new ResizeObserver(recompute);
    ro.observe(parent);
    return () => ro.disconnect();
  }, []);

  // Keep the active tick in view when the rail itself has to scroll.
  // Done by hand rather than ``scrollIntoView`` so only the track moves —
  // ``scrollIntoView`` walks every scrollable ancestor, and the one above
  // us is the transcript.
  useEffect(() => {
    const track = trackRef.current;
    const tick = tickRefs.current[activeIndex];
    if (!track || !tick) return;
    if (tick.offsetTop < track.scrollTop) {
      track.scrollTop = tick.offsetTop;
    } else if (
      tick.offsetTop + tick.offsetHeight >
      track.scrollTop + track.clientHeight
    ) {
      track.scrollTop = tick.offsetTop + tick.offsetHeight - track.clientHeight;
    }
  }, [activeIndex, turns.length]);

  /** Centre of a tick, relative to the rail root. */
  const anchorTopOf = useCallback((index: number): number => {
    const tick = tickRefs.current[index];
    const root = rootRef.current;
    if (!tick || !root) return 0;
    const rect = tick.getBoundingClientRect();
    return rect.top + rect.height / 2 - root.getBoundingClientRect().top;
  }, []);

  /** Snap the magnification to a whole tick. The per-tick ``mouseenter``
   * is the coarse signal (and the only one available to keyboard focus
   * and to jsdom, where layout is all zeroes); ``mousemove`` below
   * refines it. */
  const focusTick = useCallback(
    (index: number) => {
      setHover((prev) =>
        prev && prev.index === index && prev.pointer === index
          ? prev
          : { pointer: index, index, anchorTop: anchorTopOf(index) },
      );
    },
    [anchorTopOf],
  );

  /**
   * Track the cursor continuously along the whole column.
   *
   * The ticks are hairlines with a tall transparent hit area, so the
   * column has no gaps — without this the accordion would collapse and
   * re-open every time the pointer crossed between two ticks. Positions
   * come from the first two ticks' pitch, which is exact for a uniform
   * list and costs two layout reads instead of one per tick.
   */
  const handleMove = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>) => {
      const first = tickRefs.current[0];
      const second = tickRefs.current[1];
      if (!first || !second) return;
      // Viewport coordinates throughout. Rects already account for the
      // track's own scroll offset, and — unlike ``offsetTop``, which is
      // measured against the nearest POSITIONED ancestor (the full-height
      // nav, not the track) — they can't silently mix two coordinate
      // spaces.
      const firstRect = first.getBoundingClientRect();
      const pitch = second.getBoundingClientRect().top - firstRect.top;
      if (pitch <= 0) return;
      const raw =
        (event.clientY - (firstRect.top + firstRect.height / 2)) / pitch;
      const pointer = clamp(raw, 0, turns.length - 1);
      const index = Math.round(pointer);
      setHover((prev) => {
        // Sub-pixel jitter would re-render every tick for no visible
        // change; a hundredth of a tick is well below one device pixel of
        // width difference.
        if (
          prev &&
          prev.index === index &&
          Math.abs(prev.pointer - pointer) < 0.01
        ) {
          return prev;
        }
        return {
          pointer,
          index,
          anchorTop:
            prev && prev.index === index ? prev.anchorTop : anchorTopOf(index),
        };
      });
    },
    [anchorTopOf, turns.length],
  );

  const hoveredTurn = hover ? turns[hover.index] : undefined;

  // Folded via the same pipeline the transcript and the activity
  // dashboard use, so the card says exactly what the turn's own segment
  // strip says. Computed for the hovered turn only.
  const hoveredPhrase = useMemo(() => {
    if (!hoveredTurn) return null;
    const items = buildSegments(hoveredTurn).flatMap((s) => s.items);
    if (items.length === 0) return null;
    return summarizeSegmentPhrase(items).phrase;
  }, [hoveredTurn]);

  if (turns.length < MIN_TURNS_FOR_RAIL || !hasRoom) {
    // Still mounted (invisible) so the width observer keeps running and
    // the rail comes back on its own when the gutter reappears.
    return <nav ref={rootRef} className="hidden" aria-hidden="true" />;
  }

  return (
    <nav
      ref={rootRef}
      aria-label={t("conversation.messageIndex")}
      className="pointer-events-none absolute inset-y-0 left-0 z-10 flex w-9 items-center"
    >
      <div
        ref={trackRef}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
        className="pointer-events-auto relative flex max-h-full w-full flex-col items-start overflow-y-auto py-3 pl-2.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {turns.map((turn, index) => {
          const isActive = index === activeIndex;
          const isHovered = hover?.index === index;
          const isNear =
            hover !== null && Math.abs(index - hover.pointer) <= MAGNIFY_RADIUS;
          const failed = Boolean(turn.failedMessage);
          const halted = Boolean(turn.cancelled || turn.interrupted);
          return (
            <button
              key={turn.id}
              type="button"
              ref={(el) => {
                tickRefs.current[index] = el;
              }}
              aria-label={t("conversation.messageIndexJump", {
                index: String(index + 1),
              })}
              aria-current={isActive ? "true" : undefined}
              onClick={() => onSelect(index)}
              onMouseEnter={() => focusTick(index)}
              onFocus={() => focusTick(index)}
              onBlur={() => setHover(null)}
              // Hairline mark, tall transparent hit area: the ticks butt
              // up against each other so the cursor never leaves the rail
              // mid-travel, and a 2px bar is still clickable.
              className="flex h-2 w-full shrink-0 items-center"
            >
              <span
                data-tick-bar=""
                style={{
                  width: tickWidth(index, hover?.pointer ?? null, activeIndex),
                }}
                className={cn(
                  "h-0.5 rounded-full transition-[width,background-color] duration-100 ease-out",
                  failed
                    ? "bg-error/70"
                    : halted
                      ? "bg-accent-amber/70"
                      : isActive
                        ? "bg-brand"
                        : isHovered
                          ? "bg-ink-body/70"
                          : isNear
                            ? "bg-ink-muted/60"
                            : "bg-ink-muted/40",
                )}
              />
            </button>
          );
        })}
      </div>

      {hover && hoveredTurn ? (
        <div
          role="tooltip"
          style={{ top: hover.anchorTop }}
          className="pointer-events-none absolute left-full z-20 ml-2 w-[280px] -translate-y-1/2 rounded-lg border border-surface-border bg-surface p-3 text-xs text-ink-body shadow-xl"
        >
          <div className="mb-1 flex items-center gap-1.5 text-2xs text-ink-meta">
            <span>#{hover.index + 1}</span>
            {hoveredTurn.userTimestamp ? (
              <span>· {formatTurnTime(hoveredTurn.userTimestamp)}</span>
            ) : null}
          </div>
          <p className="line-clamp-3 whitespace-pre-wrap break-words leading-snug">
            {turnPreviewText(hoveredTurn.userText, PREVIEW_CHARS) ||
              hoveredTurn.attachments?.[0]?.name ||
              t("conversation.messageIndexNoText")}
          </p>
          {hoveredPhrase ? (
            <p className="mt-1.5 truncate text-2xs text-ink-meta">
              {hoveredPhrase}
            </p>
          ) : null}
        </div>
      ) : null}
    </nav>
  );
}
