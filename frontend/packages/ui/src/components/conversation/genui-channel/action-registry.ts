/**
 * The host seam for user-initiated actions inside generated UI.
 *
 * A2UI components already declare actions (`{event: {name, context}}`) and
 * the message processor dispatches them — but the renderer used to create the
 * processor without a listener, so every click died in the runtime. This
 * registry mirrors `host-registry.ts` (the data seam): the OSS renderer knows
 * *when* an action fired and *which* surface/component fired it; only the
 * host application knows what a given action name means. A host (a page, an
 * edition overlay) registers one sink at boot; the renderer forwards every
 * action with its render-context identity attached.
 *
 * Typical use: a workbench page turns an `ask_agent` action authored into a
 * template's empty-state card into a composer prefill. Nothing here assumes
 * that meaning — names and context travel verbatim.
 */

export interface GenUIActionEvent {
  /** The action's declared event name, verbatim from the document. */
  name: string;
  surfaceId: string;
  sourceComponentId: string;
  /** The action's declared context payload, verbatim (may be empty). */
  context: Record<string, unknown>;
  /**
   * Host render-context values the renderer was mounted with (`hostParams`) —
   * the same identity `$host` data-ref params resolve against. Absent when
   * the rendering host provided none.
   */
  host?: Record<string, string | number | boolean>;
}

export type GenUIActionSink = (event: GenUIActionEvent) => void;

let sink: GenUIActionSink | undefined;

export function registerGenUIActionSink(next: GenUIActionSink): void {
  sink = next;
}

export function unregisterGenUIActionSink(): void {
  sink = undefined;
}

export function getGenUIActionSink(): GenUIActionSink | undefined {
  return sink;
}

/** Renderer-side dispatch: silently a no-op until a host registers. */
export function dispatchGenUIAction(event: GenUIActionEvent): void {
  sink?.(event);
}
