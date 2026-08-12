import { Maximize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "../../hooks/use-i18n";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Spinner } from "../ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";
import { GenerativeUIRenderer } from "./GenerativeUIRenderer";
import { parseGenerativeUIPayload } from "./generative-ui-payload";

export interface GenerativeUICardProps {
  a2ui?: string;
  status?: "running" | "success" | "error";
  thinking?: string;
}

export const GENERATIVE_UI_LAYOUT_CSS = `
  [data-a2ui-scope="generative-ui"] {
    min-width: 0;
    max-width: 100%;
    container-type: inline-size;
    container-name: genui-inline;
  }
  [data-a2ui-scope="generative-ui"] * { box-sizing: border-box; min-width: 0; }
  [data-a2ui-scope="generative-ui"] .valuz-a2ui { max-width: 100%; }
`;

export function GenerativeUICard({ a2ui, status, thinking }: GenerativeUICardProps) {
  const { t } = useI18n();
  const [fullscreenOpen, setFullscreenOpen] = useState(false);
  const payload = parseGenerativeUIPayload(a2ui);
  const body = payload?.body ?? "";
  const cardTitle = t("genui.cardTitle" as Parameters<typeof t>[0]);
  const fullscreenLabel = t("genui.fullscreen" as Parameters<typeof t>[0]);
  const showThinking = status === "running" && Boolean(thinking);
  const thinkingRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = thinkingRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [thinking]);

  return (
    <div
      data-slot="generative-ui-card"
      data-a2ui-scope="generative-ui"
      className="overflow-hidden rounded-xl border border-surface-border bg-surface"
    >
      <style>{GENERATIVE_UI_LAYOUT_CSS}</style>
      <div className="flex items-center justify-between gap-2 border-b border-surface-border px-3 py-2">
        <span className="min-w-0 truncate text-sm font-medium text-ink-heading">{cardTitle}</span>
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label={fullscreenLabel}
                title={fullscreenLabel}
                disabled={!body}
                onClick={() => setFullscreenOpen(true)}
                className="shrink-0 text-ink-muted hover:text-ink-heading"
              >
                <Maximize2 className="size-3.5" aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">{fullscreenLabel}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
      {showThinking ? (
        <div className="border-b border-surface-border bg-surface-soft px-3 py-2">
          <div className="flex items-center gap-2 text-xs text-ink-meta">
            <Spinner className="size-3" />
            {t("conversation.thinking" as Parameters<typeof t>[0])}
          </div>
          <div
            ref={thinkingRef}
            data-testid="genui-thinking"
            className="mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap text-xs italic text-ink-meta"
          >
            {thinking}
          </div>
        </div>
      ) : null}
      {body || !showThinking ? (
        <div className="min-w-0 overflow-x-auto p-3 [&>*]:min-w-0 [&>*]:max-w-full">
          {body ? (
            <GenerativeUIRenderer payload={payload} status={status} />
          ) : (
            <div data-testid="genui-empty" className="flex items-center gap-2 text-sm text-ink-meta">
              {status === "running" ? <><Spinner className="size-3.5" />{t("genui.generating" as Parameters<typeof t>[0])}</> : t("genui.empty" as Parameters<typeof t>[0])}
            </div>
          )}
        </div>
      ) : null}
      <Dialog open={fullscreenOpen} onOpenChange={setFullscreenOpen}>
        <DialogContent className="top-9 right-4 bottom-4 left-4 h-auto max-h-none w-auto max-w-none translate-x-0 translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-none">
          <DialogHeader className="border-b border-surface-border px-4 py-3 pr-12">
            <DialogTitle className="text-sm leading-5">{cardTitle}</DialogTitle>
            <DialogDescription className="sr-only">{t("genui.fullscreenDescription" as Parameters<typeof t>[0])}</DialogDescription>
          </DialogHeader>
          <div
            data-testid="genui-fullscreen"
            data-slot="generative-ui-fullscreen"
            data-a2ui-scope="generative-ui"
            className="min-h-0 flex-1 overflow-auto p-4 [&>*]:min-w-0 [&>*]:max-w-full"
          >
            {body ? <GenerativeUIRenderer payload={payload} status={status} /> : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
