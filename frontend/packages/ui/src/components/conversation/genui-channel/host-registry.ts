/**
 * The edition seam for live data (M2). The renderer knows *when* a surface
 * declares component-owned data refs and *where* pushed updates go; only an
 * edition knows how to fetch a source. An edition registers one factory at
 * boot — the same moment it registers its blocks — and the renderer calls it
 * per surface that carries component `dataRefs` metadata.
 *
 * The factory returns null to decline (no refs it recognises), or a handle
 * whose `stop()` the renderer calls on unmount. Each named input is handed over
 * unparsed with its owning component identity; the registry contract lives in
 * the edition and the OSS renderer has no opinion about its source grammar.
 */

export interface GenUIDataHostHandle {
  stop: () => void;
}

export interface GenUIComponentDataRef {
  componentId: string;
  component: string;
  /** Developer-declared input name inside the persisted component dataRefs. */
  inputKey: string;
  ref: unknown;
}

export interface GenUIDataHostInput {
  surfaceId: string;
  /** Component-owned host metadata, verbatim; the edition validates it. */
  dataRefs: GenUIComponentDataRef[];
  /** Push one A2UI message (typically updateDataModel) into the render stream. */
  push: (message: Record<string, unknown>) => void;
  /**
   * Host render-context values for `$host` param resolution
   * (dataRef.RenderContext.host) — e.g. a company page passes its canonical
   * symbol so a param written as {"$host":"symbol"} follows the page. Absent
   * when the rendering host provides no context; `$host` refs then reject
   * at registration instead of fetching a wrong query.
   */
  host?: Record<string, string | number | boolean>;
}

export type GenUIDataHostFactory = (
  input: GenUIDataHostInput,
) => GenUIDataHostHandle | null;

let factory: GenUIDataHostFactory | undefined;

export function registerGenUIDataHost(next: GenUIDataHostFactory): void {
  factory = next;
}

export function unregisterGenUIDataHost(): void {
  factory = undefined;
}

export function getGenUIDataHost(): GenUIDataHostFactory | undefined {
  return factory;
}
