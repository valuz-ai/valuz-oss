import { Suspense, lazy, type ReactNode } from "react";

import {
  parseGenerativeUIPayload,
  type GenerativeUIPayload,
} from "./generative-ui-payload";
import { Skeleton } from "../ui/skeleton";

export type {
  GenerativeUIPayload,
  GenerativeUIProtocol,
} from "./generative-ui-payload";

export type GenerativeUIStatus = "running" | "success" | "error";

export interface GenerativeUIRendererProps {
  payload: string | GenerativeUIPayload | undefined | null;
  status?: GenerativeUIStatus;
  /** Host render-context values for `$host` data-ref params, forwarded to
   *  the A2UI renderer's edition data host (e.g. {symbol: "US:NVDA"} on a
   *  company workbench). */
  hostParams?: Record<string, string | number | boolean>;
}

/**
 * The standalone A2UI catalog and renderer are loaded lazily so their chart
 * and interaction implementations stay out of the initial application chunk.
 */
const A2UIBody = lazy(() => import("./A2UIBody"));

function GenerationSkeleton() {
  return (
    <div
      data-slot="a2ui-generation-skeleton"
      aria-hidden
      className="min-w-0 space-y-4"
    >
      <div className="space-y-2">
        <Skeleton className="h-6 w-56 max-w-full" />
        <Skeleton className="h-3.5 w-36 max-w-full" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="space-y-2.5 rounded-xl border border-surface-border p-4"
          >
            <Skeleton className="h-4 w-24 max-w-full" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function GenerativeUIRenderer({
  payload,
  status,
  hostParams,
}: GenerativeUIRendererProps) {
  const parsed = parseGenerativeUIPayload(payload);
  // Nothing to draw rather than raw text on screen: a tool result that is not
  // an A2UI stream has no renderer, and printing its source where a rendered UI
  // belongs reads as a bug in the answer rather than in the payload.
  //
  // EXCEPT while the run is live. An empty payload there is a WAIT, not an
  // absence — the model routinely reasons for a minute before writing its
  // first byte, and returning null through all of it leaves the surface that
  // is supposed to be showing the generation completely blank. Hand the empty
  // body down so ``A2UIRenderer`` makes the call in ONE place: it breathes a
  // skeleton until something resolves, here and everywhere else.
  if (!parsed?.body) {
    if (status !== "running") return null;
    return <A2UIBodyWrapper body="" status={status} hostParams={hostParams} />;
  }

  return (
    <A2UIBodyWrapper
      body={parsed.body}
      status={status}
      hostParams={hostParams}
    />
  );
}

function A2UIBodyWrapper({
  body,
  status,
  hostParams,
}: {
  body: string;
  status?: GenerativeUIStatus;
  hostParams?: Record<string, string | number | boolean>;
}) {
  return (
    <SuspenseWrapper>
      <A2UIBody
        body={body}
        status={status === "running" ? "running" : "success"}
        hostParams={hostParams}
      />
    </SuspenseWrapper>
  );
}

function SuspenseWrapper({ children }: { children: ReactNode }) {
  return <Suspense fallback={<GenerationSkeleton />}>{children}</Suspense>;
}
