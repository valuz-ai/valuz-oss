import {
  createValuzMessageProcessor,
  effectiveA2UIComponentNames,
  getA2UIRegistryVersion,
  subscribeA2UIComponents,
  ValuzA2UISurface,
  type ReactComponentImplementation,
  type SurfaceModel,
} from "@valuz/a2ui";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { Skeleton } from "../ui/skeleton";
import { getGenUIDataHost } from "./genui-channel/host-registry";
import { completeJsonFragment } from "./partial-json";

export interface A2UIRendererProps {
  body: string;
  status?: "running" | "success";
  hostParams?: Record<string, string | number | boolean>;
}

type A2UIMessage = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function safeJsonParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

function looksLikeA2UIMessage(value: Record<string, unknown>): boolean {
  return ["createSurface", "updateComponents", "updateDataModel", "deleteSurface"].some(
    (key) => key in value,
  );
}

function salvagePartialMessage(line: string): A2UIMessage | null {
  const completed = completeJsonFragment(line);
  if (!completed) return null;
  const parsed = safeJsonParse(completed);
  return isRecord(parsed) && looksLikeA2UIMessage(parsed) ? parsed : null;
}

function parseA2UIMessages(body: string): A2UIMessage[] {
  const trimmed = body.trim();
  if (!trimmed) return [];
  const parsed = safeJsonParse(trimmed);
  if (Array.isArray(parsed)) return parsed.filter(isRecord);
  if (isRecord(parsed) && Array.isArray(parsed.messages)) return parsed.messages.filter(isRecord);
  if (isRecord(parsed) && looksLikeA2UIMessage(parsed)) return [parsed];

  const messages: A2UIMessage[] = [];
  for (const rawLine of trimmed.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line.startsWith("{")) continue;
    const value = safeJsonParse(line);
    if (isRecord(value)) messages.push(value);
    else {
      const partial = salvagePartialMessage(line);
      if (partial) messages.push(partial);
    }
  }
  return messages;
}

function collectDataRefs(body: string): Map<string, Record<string, unknown>> {
  const bySurface = new Map<string, Record<string, unknown>>();
  for (const message of parseA2UIMessages(body)) {
    const update = message.updateDataModel;
    if (!isRecord(update) || typeof update.surfaceId !== "string" || typeof update.path !== "string") continue;
    const existing = bySurface.get(update.surfaceId) ?? {};
    if (update.path === "/refs" && isRecord(update.value)) {
      bySurface.set(update.surfaceId, { ...existing, ...update.value });
    } else if (update.path.startsWith("/refs/")) {
      const slot = update.path.slice(6);
      if (slot && !slot.includes("/")) bySurface.set(update.surfaceId, { ...existing, [slot]: update.value });
    }
  }
  return bySurface;
}

function dropDuplicateCreateSurface(messages: A2UIMessage[]): A2UIMessage[] {
  const seen = new Set<string>();
  return messages.filter((message) => {
    if (!isRecord(message.createSurface)) return true;
    const id = message.createSurface.surfaceId;
    if (typeof id !== "string" || !seen.has(id)) {
      if (typeof id === "string") seen.add(id);
      return true;
    }
    return false;
  });
}

function dropRepeatedDocument(body: string): string {
  const text = body.trimEnd();
  const firstLineEnd = text.indexOf("\n");
  if (firstLineEnd < 0) return body;
  const head = text.slice(0, firstLineEnd);
  let from = firstLineEnd;
  for (;;) {
    const at = text.indexOf(`\n${head}`, from);
    if (at < 0) return body;
    const first = text.slice(0, at);
    const second = text.slice(at + 1);
    if (first.startsWith(second)) return first;
    from = at + 1;
  }
}

function hasPartialTrailingLine(body: string): boolean {
  const line = body.trimEnd().split(/\r?\n/).at(-1)?.trim();
  if (!line) return false;
  return safeJsonParse(line) === undefined;
}

function withoutUnreadyComponents(messages: A2UIMessage[], trailingIsPartial: boolean): A2UIMessage[] {
  const known = new Set(effectiveA2UIComponentNames());
  const trailingIndex = trailingIsPartial ? messages.length - 1 : -1;
  const declared = new Set<string>();

  messages.forEach((message, messageIndex) => {
    if (!isRecord(message.updateComponents) || !Array.isArray(message.updateComponents.components)) return;
    for (const component of message.updateComponents.components) {
      if (!isRecord(component) || typeof component.id !== "string") continue;
      if (messageIndex === trailingIndex && !known.has(String(component.component ?? ""))) continue;
      declared.add(component.id);
    }
  });
  if (declared.size === 0) return [];

  return messages.map((message, messageIndex) => {
    const update = message.updateComponents;
    if (!isRecord(update) || !Array.isArray(update.components)) return message;
    const components = update.components
      .filter((component) =>
        isRecord(component) &&
        typeof component.id === "string" &&
        typeof component.component === "string" &&
        (messageIndex !== trailingIndex || known.has(component.component)),
      )
      .map((component) => {
        if (!isRecord(component) || !Array.isArray(component.children)) return component;
        return {
          ...component,
          children: component.children.filter((child) => typeof child !== "string" || declared.has(child)),
        };
      });
    return { ...message, updateComponents: { ...update, components } };
  });
}

function buildSurfaces(
  body: string,
  liveMessages: A2UIMessage[] = [],
): SurfaceModel<ReactComponentImplementation>[] {
  const deduped = dropRepeatedDocument(body);
  const messages = [
    ...withoutUnreadyComponents(
      dropDuplicateCreateSurface(parseA2UIMessages(deduped)),
      hasPartialTrailingLine(deduped),
    ),
    ...liveMessages,
  ];
  if (!messages.length) return [];
  const processor = createValuzMessageProcessor();
  try {
    processor.processMessages(messages as never);
  } catch (error) {
    if (import.meta.env.DEV) console.warn("[a2ui] failed to render payload", error);
    return [];
  }
  return Array.from(processor.model.surfacesMap.values());
}

function GenerationSkeleton() {
  return (
    <div data-slot="a2ui-generation-skeleton" aria-hidden className="min-w-0 space-y-4">
      <div className="space-y-2"><Skeleton className="h-6 w-56 max-w-full" /><Skeleton className="h-3.5 w-36 max-w-full" /></div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((index) => <div key={index} className="space-y-2.5 rounded-xl border border-surface-border p-4"><Skeleton className="h-4 w-24 max-w-full" /><Skeleton className="h-3 w-full" /><Skeleton className="h-3 w-4/5" /></div>)}
      </div>
    </div>
  );
}

function GenerationTail() {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let parent = element.parentElement;
    while (parent) {
      const overflow = getComputedStyle(parent).overflowY;
      if ((overflow === "auto" || overflow === "scroll") && parent.scrollHeight > parent.clientHeight) {
        if (parent.scrollHeight - parent.scrollTop - parent.clientHeight > 240) return;
        break;
      }
      parent = parent.parentElement;
    }
    element.scrollIntoView({ block: "end", behavior: "smooth" });
  });
  return <div ref={ref} data-slot="a2ui-generation-tail" className="flex items-center gap-2 px-1 py-3"><Skeleton className="h-3 w-32" /></div>;
}

export function A2UIRenderer({ body, status, hostParams }: A2UIRendererProps) {
  const version = useSyncExternalStore(
    subscribeA2UIComponents,
    getA2UIRegistryVersion,
    getA2UIRegistryVersion,
  );
  const [liveMessages, setLiveMessages] = useState<A2UIMessage[]>([]);
  useEffect(() => setLiveMessages([]), [body]);

  const hostParamsKey = hostParams
    ? JSON.stringify(Object.keys(hostParams).sort().map((key) => [key, hostParams[key]]))
    : "";
  useEffect(() => {
    const factory = getGenUIDataHost();
    if (!factory) return;
    const handles = Array.from(collectDataRefs(body), ([surfaceId, refs]) =>
      factory({
        surfaceId,
        refs,
        push: (message) => setLiveMessages((previous) => [...previous, message]),
        ...(hostParams ? { host: hostParams } : {}),
      }),
    ).filter(Boolean);
    return () => handles.forEach((handle) => handle?.stop());
    // hostParamsKey is the stable representation of hostParams.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [body, version, hostParamsKey]);

  const built = useMemo(() => buildSurfaces(body, liveMessages), [body, version, liveMessages]);
  const lastGood = useRef<{ body: string; surfaces: SurfaceModel<ReactComponentImplementation>[] }>({ body: "", surfaces: [] });
  if (built.length) lastGood.current = { body, surfaces: built };
  const inherits = lastGood.current.surfaces.length > 0 && body.startsWith(lastGood.current.body);
  const surfaces = built.length || status !== "running" || !inherits ? built : lastGood.current.surfaces;

  if (!surfaces.length) return status === "running" ? <GenerationSkeleton /> : null;
  return (
    <div data-slot="a2ui-renderer">
      {surfaces.map((surface) => <ValuzA2UISurface key={surface.id} surface={surface} />)}
      {status === "running" ? <GenerationTail /> : null}
    </div>
  );
}
