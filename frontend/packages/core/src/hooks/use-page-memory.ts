/**
 * Page memory: a list page keeps its data, filters, selection and scroll
 * position when the user opens a detail route and comes back.
 *
 * Route changes unmount the page component, so plain `useState` is lost and
 * the return trip shows a full-page spinner and lands at the top. These hooks
 * park the state worth keeping in a module-level map and re-initialize from
 * it on the next mount. The page's own fetch effects keep running, so the
 * previous data shows immediately and a background refresh replaces it.
 *
 * Usage:
 *   const [items, setItems] = usePageMemoryState<Item[] | null>("thesis:list", null);
 *   const rootRef = useRestoredScroll("thesis", items !== null);
 *   return <div ref={rootRef}>…</div>;
 *
 * Conventions:
 * - Keys are global; name them `page:field`. Data that depends on a selection
 *   puts the id in the key (`watchlist:view:${id}`) — when the key changes the
 *   hook switches to that memory, or to the initial value if there is none.
 * - Memory is not partitioned per account, like the request cache. After an
 *   account switch the first frame may show the old data; the background
 *   refresh that follows replaces it.
 * - A data hook that dedupes with a "requested once" set must let each batch
 *   land instead of discarding it on effect cleanup: with rows present on the
 *   first mount, StrictMode's double effect run would otherwise drop the first
 *   batch and filter the second, leaving the data empty forever.
 */
import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

const memory = new Map<string, unknown>();

function resolveInitial<T>(initial: T | (() => T)): T {
  return typeof initial === "function" ? (initial as () => T)() : initial;
}

/** Synchronous read, for deriving an initial selection from a remembered list. */
export function readPageMemory<T>(key: string): T | undefined {
  return memory.get(key) as T | undefined;
}

/** Test helper: forget everything. */
export function resetPageMemory(): void {
  memory.clear();
}

/** Same signature as `useState`, but the value survives unmount under `key`. */
export function usePageMemoryState<T>(
  key: string,
  initial: T | (() => T),
): [T, Dispatch<SetStateAction<T>>] {
  const read = (): T =>
    memory.has(key) ? (memory.get(key) as T) : resolveInitial(initial);
  const [value, setValue] = useState<T>(read);
  // A changed key (the user picked another list) swaps in that memory. Setting
  // state during render is React's sanctioned "adjust state on prop change"
  // pattern; it does not paint a frame with the stale value.
  const [trackedKey, setTrackedKey] = useState(key);
  if (trackedKey !== key) {
    setTrackedKey(key);
    setValue(read());
  }
  const set = useCallback<Dispatch<SetStateAction<T>>>(
    (next) => {
      setValue((previous) => {
        const resolved =
          typeof next === "function"
            ? (next as (previous: T) => T)(previous)
            : next;
        memory.set(key, resolved);
        return resolved;
      });
    },
    [key],
  );
  return [value, set];
}

/**
 * Pages usually do not scroll themselves; the shell's content container does.
 * Walk up from the node to the first scrollable element.
 */
function scrollableFrom(node: HTMLElement): HTMLElement | null {
  let current: HTMLElement | null = node;
  while (current && current !== document.body) {
    const { overflowY } = getComputedStyle(current);
    if (/(auto|scroll)/.test(overflowY)) return current;
    current = current.parentElement;
  }
  return null;
}

/**
 * Remember and restore a scroll position. Attach the returned ref to the page
 * root (or to a container that scrolls on its own). Restoration waits for
 * `ready`: the content must have height first or `scrollTop` clamps to 0.
 * Data rendered synchronously from memory is ready on the first frame, so the
 * restored position is not pushed around by content arriving later.
 */
export function useRestoredScroll(
  key: string,
  ready = true,
): (node: HTMLElement | null) => void {
  // The root often does not render before data is ready; a state-backed ref
  // attaches to the container when the root appears.
  const [node, setNode] = useState<HTMLElement | null>(null);
  const restoredKeyRef = useRef<string | null>(null);
  useLayoutEffect(() => {
    const container = node ? scrollableFrom(node) : null;
    if (!container || !ready) return;
    const memoryKey = `scroll:${key}`;
    if (restoredKeyRef.current !== key) {
      restoredKeyRef.current = key;
      container.scrollTop = (memory.get(memoryKey) as number | undefined) ?? 0;
    }
    const onScroll = () => {
      memory.set(memoryKey, container.scrollTop);
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => container.removeEventListener("scroll", onScroll);
  }, [key, node, ready]);
  return setNode;
}
