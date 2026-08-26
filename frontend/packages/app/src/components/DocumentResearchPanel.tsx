import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  AlertTriangle,
  Loader2,
  RefreshCw,
  Send,
  Share2,
} from "lucide-react";
import {
  documentResearchApi,
  sessionsApi,
  useTranslation,
} from "@valuz/core";
import type {
  CitationRefV1,
  DocumentResearchSessionV1,
  DocumentSummaryArtifactV1,
  OpenCitationInput,
} from "@valuz/shared";
import {
  Button,
  MarkdownContent,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  type DocumentSource,
  cn,
  usePersistentScroll,
} from "@valuz/ui";

import { SessionStreamView } from "./SessionStreamView";

type ResearchTab = "summary" | "qa";
const SUMMARY_PROFILE = "brief" as const;

export interface DocumentResearchPanelProps {
  document: DocumentSource | null;
  resolutionNotice?: string | null;
  originSessionId?: string | null;
  originMessageId?: string | null;
  onCitationClick?: (
    sessionId: string,
    input: OpenCitationInput,
  ) => void;
  onDocumentCitationClick?: (citation: CitationRefV1) => void;
}

const wait = (ms: number, signal: AbortSignal): Promise<void> =>
  new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });

export function DocumentResearchPanel({
  document,
  resolutionNotice,
  originSessionId,
  originMessageId,
  onCitationClick,
  onDocumentCitationClick,
}: DocumentResearchPanelProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<ResearchTab>("summary");
  const [summary, setSummary] =
    useState<DocumentSummaryArtifactV1 | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [researchSession, setResearchSession] =
    useState<DocumentResearchSessionV1 | null>(null);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [streamVersion, setStreamVersion] = useState(0);
  const [streamActive, setStreamActive] = useState(false);
  const [summaryAttempt, setSummaryAttempt] = useState(0);
  const [sharing, setSharing] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);
  const [shareComplete, setShareComplete] = useState(false);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const summaryScrollRef = useRef<HTMLDivElement>(null);
  usePersistentScroll(
    summaryScrollRef,
    document
      ? `valuz.reader.summaryScroll:${document.id}:${SUMMARY_PROFILE}`
      : null,
    Boolean(summary) && !summaryLoading,
  );

  const ensureResearchSession = useCallback(
    async (signal?: AbortSignal): Promise<DocumentResearchSessionV1> => {
      if (!document) throw new Error("document_unavailable");
      if (researchSession?.document_ids[0] === document.id)
        return researchSession;
      setSessionLoading(true);
      setSessionError(null);
      try {
        const value = await documentResearchApi.getOrCreateSession(
          document.id,
          { originSessionId, originMessageId },
          signal ? { signal } : {},
        );
        setResearchSession(value);
        return value;
      } catch (cause) {
        if (!signal?.aborted) {
          const message =
            cause instanceof Error ? cause.message : "research_session_failed";
          setSessionError(message);
        }
        throw cause;
      } finally {
        if (!signal?.aborted) setSessionLoading(false);
      }
    },
    [
      document,
      originMessageId,
      originSessionId,
      researchSession,
    ],
  );

  useEffect(() => {
    if (!document) return;
    const controller = new AbortController();
    setSummary(null);
    setSummaryLoading(true);
    setSummaryError(null);
    setResearchSession(null);
    setStreamVersion(0);
    setStreamActive(false);
    setShareError(null);
    setShareComplete(false);

    const load = async () => {
      try {
        let value = await documentResearchApi.getSummary(
          document.id,
          SUMMARY_PROFILE,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        if (value) {
          setSummary(value);
          if (value.research_session_id) {
            setResearchSession({
              session_id: value.research_session_id,
              purpose: "document-research",
              document_ids: [document.id],
              document_versions: [value.document_version],
              source_scope: "locked",
              origin_session_id: originSessionId ?? null,
              origin_message_id: originMessageId ?? null,
              reused: true,
            });
          }
        }
        if (!value || value.status === "stale" || value.status === "failed") {
          value = await documentResearchApi.generateSummary(
            document.id,
            {
              profile: SUMMARY_PROFILE,
              force: value?.status === "failed",
              originSessionId,
              originMessageId,
            },
            { signal: controller.signal },
          );
          if (controller.signal.aborted) return;
          setSummary(value);
        }
        for (
          let attempt = 0;
          value?.status === "pending" && attempt < 30;
          attempt += 1
        ) {
          await wait(1000, controller.signal);
          value = await documentResearchApi.getSummary(
            document.id,
            SUMMARY_PROFILE,
            { signal: controller.signal },
          );
          if (value) setSummary(value);
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setSummaryError(
            cause instanceof Error ? cause.message : "summary_failed",
          );
        }
      } finally {
        if (!controller.signal.aborted) setSummaryLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, [
    document,
    originMessageId,
    originSessionId,
    summaryAttempt,
  ]);

  useEffect(() => {
    if (tab !== "qa" || !document || researchSession) return;
    const controller = new AbortController();
    void ensureResearchSession(controller.signal).catch(() => undefined);
    return () => controller.abort();
  }, [document, ensureResearchSession, researchSession, tab]);

  const submitQuestion = async (event: FormEvent) => {
    event.preventDefault();
    const value = question.trim();
    if (!value || sending || !document) return;
    setSending(true);
    setSessionError(null);
    try {
      const session = await ensureResearchSession();
      setQuestion("");
      setStreamActive(true);
      setStreamVersion((current) => current + 1);
      await sessionsApi.sendMessage(session.session_id, value);
    } catch (cause) {
      setSessionError(
        cause instanceof Error ? cause.message : "question_failed",
      );
      setStreamActive(false);
    } finally {
      setSending(false);
      questionRef.current?.focus();
    }
  };

  const shareToOrigin = async () => {
    const sessionId =
      tab === "summary"
        ? summary?.research_session_id
        : researchSession?.session_id;
    if (!originSessionId || !sessionId || sharing) return;
    setSharing(true);
    setShareError(null);
    setShareComplete(false);
    try {
      await documentResearchApi.shareToOrigin(
        sessionId,
        tab === "summary" ? summary?.message_id : undefined,
      );
      setShareComplete(true);
    } catch (cause) {
      setShareError(
        cause instanceof Error ? cause.message : "research_share_failed",
      );
    } finally {
      setSharing(false);
    }
  };

  const renderSummary = () => {
    if ((summaryLoading && !summary) || summary?.status === "pending") {
      return (
        <div className="flex flex-1 items-center justify-center" role="status">
          <Loader2 className="h-4 w-4 animate-spin text-ink-meta" />
          <span className="sr-only">{t("common.loading")}</span>
        </div>
      );
    }
    if (summaryError && !summary) {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-5 text-center">
          <AlertTriangle className="h-5 w-5 text-warning-text" />
          <p className="text-xs leading-5 text-ink-meta">
            {t("common.error")}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setSummaryAttempt((value) => value + 1)}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            {t("common.retry")}
          </Button>
        </div>
      );
    }
    if (!summary) {
      return (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-xs text-ink-meta">
          {t("ui.reader.summaryEmpty" as Parameters<typeof t>[0])}
        </div>
      );
    }
    if (!summary.content.trim()) {
      const providerUnavailable = summary.error_message
        ?.split(";")
        .some((message) => message.trim() === "provider_summary_unavailable");
      if (providerUnavailable || summary.status !== "failed") {
        return (
          <div className="flex flex-1 items-center justify-center px-6 text-center text-xs text-ink-meta">
            {t("ui.reader.summaryEmpty" as Parameters<typeof t>[0])}
          </div>
        );
      }
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-5 text-center">
          <AlertTriangle className="h-5 w-5 text-warning-text" />
          <p className="text-xs leading-5 text-ink-meta">
            {t("common.error")}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setSummaryAttempt((value) => value + 1)}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            {t("common.retry")}
          </Button>
        </div>
      );
    }
    const bundle =
      summary.citation_bundle?.version === 1
        ? summary.citation_bundle
        : undefined;
    const degraded =
      summary.status === "degraded" ||
      summary.status === "failed" ||
      summary.status === "stale";
    return (
      <div
        ref={summaryScrollRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
      >
        {degraded ? (
          <div className="mb-3 rounded-md border border-warning/30 bg-warning-light px-3 py-2 text-xs text-warning-text">
            {summary.status === "stale"
              ? t("ui.reader.summaryStale" as Parameters<typeof t>[0])
              : t("ui.reader.summaryDegraded" as Parameters<typeof t>[0])}
          </div>
        ) : null}
        <MarkdownContent
          content={summary.content}
          citationBundle={bundle}
          messageId={summary.message_id ?? undefined}
          onCitationClick={(input) => {
            if (summary.research_session_id) {
              onCitationClick?.(summary.research_session_id, input);
              return;
            }
            const citation = bundle?.citations.find(
              (item) => item.citationId === input.citationId,
            );
            if (citation) onDocumentCitationClick?.(citation);
          }}
          className="text-sm"
        />
      </div>
    );
  };

  const renderQa = () => (
    <div className="flex min-h-0 flex-1 flex-col">
      {sessionError ? (
        <div className="border-b border-danger/30 bg-danger-light px-3 py-2 text-xs text-danger-text">
          {sessionError}
        </div>
      ) : null}
      <div className="min-h-0 flex-1">
        {researchSession ? (
          <SessionStreamView
            key={`${researchSession.session_id}:${streamVersion}`}
            sessionId={researchSession.session_id}
            heightClass="h-full rounded-none border-0"
            active={streamActive}
            scrollStorageKey={`valuz.reader.qaScroll:${document?.id ?? "unknown"}:${researchSession.session_id}`}
            onIdle={() => setStreamActive(false)}
            onCitationClick={(input) =>
              onCitationClick?.(researchSession.session_id, input)
            }
          />
        ) : (
          <div className="flex h-full items-center justify-center px-6 text-center">
            {sessionLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-ink-meta" />
            ) : (
              <p className="text-xs leading-5 text-ink-meta">
                {t("ui.reader.qaEmpty" as Parameters<typeof t>[0])}
              </p>
            )}
          </div>
        )}
      </div>
      <form
        onSubmit={submitQuestion}
        className="shrink-0 bg-surface p-3"
      >
        <div className="flex items-end gap-2 rounded-lg border border-surface-border bg-surface px-2 py-1.5 focus-within:border-accent">
          <textarea
            ref={questionRef}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            rows={2}
            disabled={sending || sessionLoading}
            aria-label={t("ui.reader.askDocument" as Parameters<typeof t>[0])}
            placeholder={t(
              "ui.reader.askDocument" as Parameters<typeof t>[0],
            )}
            className="min-h-10 flex-1 resize-none bg-transparent px-1 py-1 text-sm text-ink-body outline-none placeholder:text-ink-meta disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={!question.trim() || sending || sessionLoading}
            aria-label={t("common.send" as Parameters<typeof t>[0])}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-accent text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </form>
    </div>
  );

  return (
    <section className="flex h-full min-h-0 flex-col bg-surface">
      <Tabs
        value={tab}
        onValueChange={(value) => {
          if (value === "summary" || value === "qa") setTab(value);
        }}
        className="h-full min-h-0 gap-0"
      >
        <div className="flex h-11 shrink-0 items-center bg-surface px-2">
          <TabsList
            variant="line"
            className="min-w-0 flex-1 border-b-0"
          >
            <TabsTrigger value="summary">
              {t("ui.reader.summary" as Parameters<typeof t>[0])}
            </TabsTrigger>
            <TabsTrigger value="qa">
              {t("ui.reader.qa" as Parameters<typeof t>[0])}
            </TabsTrigger>
          </TabsList>
          {originSessionId ? (
            <button
              type="button"
              onClick={() => void shareToOrigin()}
              disabled={
                sharing ||
                (tab === "summary"
                  ? !summary?.message_id || !summary.research_session_id
                  : !researchSession || streamActive || sending)
              }
              aria-label={t(
                "ui.reader.sendToMainChat" as Parameters<typeof t>[0],
              )}
              title={t(
                "ui.reader.sendToMainChat" as Parameters<typeof t>[0],
              )}
              className={cn(
                "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition",
                shareComplete
                  ? "text-success"
                  : "text-ink-meta hover:bg-surface-muted hover:text-ink-heading",
                "disabled:cursor-not-allowed disabled:opacity-40",
              )}
            >
              {sharing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Share2 className="h-3.5 w-3.5" />
              )}
            </button>
          ) : null}
        </div>
        {shareComplete ? (
          <div
            role="status"
            className="border-b border-success/30 bg-success-light px-3 py-2 text-xs text-success"
          >
            {t("ui.reader.sentToMainChat" as Parameters<typeof t>[0])}
          </div>
        ) : null}
        {shareError ? (
          <div
            role="alert"
            className="border-b border-danger/30 bg-danger-light px-3 py-2 text-xs text-danger-text"
          >
            {shareError}
          </div>
        ) : null}
        {resolutionNotice ? (
          <div className="border-b border-warning/30 bg-warning-light px-3 py-2 text-xs text-warning-text">
            {resolutionNotice}
          </div>
        ) : null}
        <TabsContent
          value="summary"
          className="flex min-h-0 flex-1 flex-col"
        >
          {renderSummary()}
        </TabsContent>
        <TabsContent value="qa" className="flex min-h-0 flex-1 flex-col">
          {renderQa()}
        </TabsContent>
      </Tabs>
    </section>
  );
}
