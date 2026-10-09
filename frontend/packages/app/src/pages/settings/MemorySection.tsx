import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Button,
  Card,
  CardContent,
  DeleteConfirmDialog,
  SettingsRow,
  SettingsSection,
  Switch,
  Textarea,
  LoadingState,
  FormDialog,
  DialogField,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@valuz/ui";
import {
  memoryApi,
  useAuthStore,
  settingsApi,
  useTranslation,
  type MemorySettings,
  type MemoryRecord,
  type MemorySourceRef,
  type MemoryRecordsPage,
  type PreferencesResponse,
} from "@valuz/core";
import { useSectionHeaderActions } from "./section-header-actions";

export const MemorySection = () => {
  const ownerId = useAuthStore((state) => state.currentUser?.id);
  // Commercial org changes publish the active sub-account into this core
  // identity store. Re-mount all memory state, drafts and confirmations together.
  return (
    <MemorySectionContent key={ownerId ?? "anonymous"} ownerId={ownerId} />
  );
};

function MemorySectionContent({ ownerId }: { ownerId: string | undefined }) {
  const headerActions = useSectionHeaderActions("personalization");
  const { t } = useTranslation();
  const alive = useRef(false);
  const readController = useRef<AbortController | null>(null);
  const sameOwner = useCallback(
    () => alive.current && useAuthStore.getState().currentUser?.id === ownerId,
    [ownerId],
  );
  const [view, setView] = useState<MemorySettings | null>(null);
  const [preferences, setPreferences] = useState<PreferencesResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<"unavailable" | "loadFailed" | "">(
    "",
  );
  // Locally-edited custom instructions; persisted on blur (see saveCustom).
  const [customInstructions, setCustomInstructions] = useState("");

  const load = useCallback(async () => {
    if (!sameOwner()) return;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    setLoading(true);
    setLoadError("");
    try {
      const [v, prefs] = await Promise.all([
        memoryApi.getSettings({ signal: controller.signal }),
        settingsApi.getPreferences(),
      ]);
      if (!sameOwner() || controller.signal.aborted) return;
      setView(v);
      setPreferences(prefs);
      setCustomInstructions(v.custom_instructions);
    } catch (cause) {
      if (!sameOwner() || controller.signal.aborted) return;
      setView(null);
      setLoadError(statusOf(cause) === 503 ? "unavailable" : "loadFailed");
      toast.error(t("settings.memory.loadFailed"));
    } finally {
      if (sameOwner() && !controller.signal.aborted) setLoading(false);
    }
  }, [t, sameOwner]);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      readController.current?.abort();
    };
  }, [load]);

  const toggle = async (key: "enabled" | "auto_extract", value: boolean) => {
    if (!sameOwner()) return;
    try {
      const next = await memoryApi.patchSettings({ [key]: value });
      if (!sameOwner()) return;
      setView((v) =>
        v
          ? { ...v, enabled: next.enabled, auto_extract: next.auto_extract }
          : v,
      );
    } catch {
      if (!sameOwner()) return;
      toast.error(t("settings.memory.saveFailed"));
    }
  };

  const toggleConversation = async (
    key:
      | "conversation_citations_enabled"
      | "conversation_verification_enabled"
      | "conversation_task_coverage_enabled"
      | "ptc_enabled",
    value: boolean,
  ) => {
    if (!sameOwner()) return;
    try {
      const next = await settingsApi.patchPreferences({ [key]: value });
      if (sameOwner()) setPreferences(next);
    } catch {
      if (!sameOwner()) return;
      toast.error(t("settings.personalization.saveFailed"));
    }
  };

  const saveCustom = async () => {
    if (!sameOwner()) return;
    const next = customInstructions.trim();
    if (next === (view?.custom_instructions ?? "")) return; // no change
    try {
      const res = await memoryApi.patchSettings({ custom_instructions: next });
      if (!sameOwner()) return;
      setCustomInstructions(res.custom_instructions);
      setView((v) =>
        v ? { ...v, custom_instructions: res.custom_instructions } : v,
      );
    } catch {
      if (!sameOwner()) return;
      toast.error(t("settings.memory.saveFailed"));
    }
  };

  const masterOn = view?.enabled ?? false;
  const citationsOn = preferences?.conversation_citations_enabled ?? true;
  const verificationOn =
    preferences?.conversation_verification_enabled ?? false;
  const taskCoverageOn =
    preferences?.conversation_task_coverage_enabled ?? false;
  const ptcOn = preferences?.ptc_enabled ?? false;

  return (
    <SettingsSection
      actions={headerActions}
      title={t("settings.tab.personalization.label")}
      desc={t("settings.tab.personalization.desc")}
    >
      <div className="mb-2 text-sm font-medium text-ink-heading">
        {t("settings.personalization.conversationTitle")}
      </div>
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.personalization.citationsLabel")}
            desc={t("settings.personalization.citationsDesc")}
          >
            <Switch
              checked={citationsOn}
              onCheckedChange={(value) =>
                void toggleConversation("conversation_citations_enabled", value)
              }
            />
          </SettingsRow>
          <div className="my-5 h-px bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.personalization.verificationLabel")}
            desc={t("settings.personalization.verificationDesc")}
          >
            <Switch
              checked={verificationOn}
              onCheckedChange={(value) =>
                void toggleConversation(
                  "conversation_verification_enabled",
                  value,
                )
              }
            />
          </SettingsRow>
          <div className="my-5 h-px bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.personalization.taskCoverageLabel")}
            desc={t("settings.personalization.taskCoverageDesc")}
          >
            <Switch
              checked={taskCoverageOn}
              onCheckedChange={(value) =>
                void toggleConversation(
                  "conversation_task_coverage_enabled",
                  value,
                )
              }
            />
          </SettingsRow>
          <div className="my-5 h-px bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.personalization.ptcLabel")}
            desc={t("settings.personalization.ptcDesc")}
          >
            <Switch
              checked={ptcOn}
              onCheckedChange={(value) =>
                void toggleConversation("ptc_enabled", value)
              }
            />
          </SettingsRow>
        </CardContent>
      </Card>

      <div className="mb-2 text-sm font-medium text-ink-heading">
        {t("settings.personalization.memoryTitle")}
      </div>
      {loadError && (
        <div className="mb-4 space-y-2">
          <p role="alert" className="text-sm text-error-text">
            {t(`settings.memory.${loadError}` as Parameters<typeof t>[0])}
          </p>
          <Button
            variant="outline"
            disabled={loading}
            onClick={() => void load()}
          >
            {t("settings.memory.refreshRecords" as Parameters<typeof t>[0])}
          </Button>
        </div>
      )}
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.memory.enabledLabel")}
            desc={t("settings.memory.enabledDesc")}
          >
            <Switch
              checked={masterOn}
              disabled={loading || !view}
              onCheckedChange={(v) => void toggle("enabled", v)}
            />
          </SettingsRow>
          <div className="my-5 h-px bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.memory.autoExtractLabel")}
            desc={t("settings.memory.autoExtractDesc")}
          >
            <Switch
              checked={view?.auto_extract ?? true}
              disabled={!masterOn}
              onCheckedChange={(v) => void toggle("auto_extract", v)}
            />
          </SettingsRow>
          <div className="my-5 h-px bg-surface-border" />
          <div className="flex flex-col gap-2">
            <div>
              <div className="text-sm font-medium text-ink-heading">
                {t("settings.memory.customInstructionsLabel")}
              </div>
              <div className="mt-0.5 text-xs text-ink-meta">
                {t("settings.memory.customInstructionsDesc")}
              </div>
            </div>
            <Textarea
              value={customInstructions}
              maxLength={1500}
              rows={3}
              disabled={!masterOn}
              placeholder={t("settings.memory.customInstructionsPlaceholder")}
              onChange={(e) => setCustomInstructions(e.target.value)}
              onBlur={() => void saveCustom()}
            />
          </div>
        </CardContent>
      </Card>

      <MemoryRecordsPanel enabled={masterOn} ownerId={ownerId} />
    </SettingsSection>
  );
}

type Mutation = {
  kind: "add" | "correct" | "forget" | "source";
  record?: MemoryRecord;
  source?: MemorySourceRef;
  content: string;
  target: "user" | "global";
  operationId?: string;
  baseRevision?: number;
  authorityId?: string | null;
  authorityEpoch?: number | null;
  conflict?: boolean;
  uncertain?: boolean;
  cleanupIncomplete?: boolean;
};

export function MemoryRecordsPanel({
  enabled,
  ownerId,
}: {
  enabled: boolean;
  ownerId?: string;
}) {
  const alive = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const sameOwner = useCallback(
    () => alive.current && useAuthStore.getState().currentUser?.id === ownerId,
    [ownerId],
  );
  const { t } = useTranslation();
  const label = (key: string) =>
    t(`settings.memory.${key}` as Parameters<typeof t>[0]);
  const [page, setPage] = useState<MemoryRecordsPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [scope, setScope] = useState<"all" | "user" | "global">("all");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<Mutation | null>(null);
  const [confirmationOpen, setConfirmationOpen] = useState(true);
  const [notice, setNotice] = useState("");
  const replayRequired = !!(pending?.uncertain || pending?.cleanupIncomplete);
  const displayedError = pending?.uncertain ? "saveUncertain" : pending?.cleanupIncomplete ? "cleanupIncomplete" : error;
  const query = {
    offset,
    limit: 20,
    ...(scope === "all" ? {} : { target: scope }),
  };

  useEffect(() => {
    if (!sameOwner()) return;
    const controller = new AbortController();
    setLoading(true);
    setError((current) => current === "cleanupIncomplete" || current === "saveUncertain" ? current : "");
    setPage(null);
    memoryApi
      .listRecords(
        { offset, limit: 20, ...(scope === "all" ? {} : { target: scope }) },
        { signal: controller.signal },
      )
      .then((next) => {
        if (!controller.signal.aborted && sameOwner()) setPage(next);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted && sameOwner()) {
          setError(statusOf(cause) === 503 ? "unavailable" : "loadFailed");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && sameOwner()) setLoading(false);
      });
    return () => controller.abort();
  }, [offset, scope, refresh, sameOwner]);

  function editContent(content: string) {
    if (busy || replayRequired) return;
    setPending((current) =>
      current
        ? {
            ...current,
            content,
            operationId: undefined,
            baseRevision: undefined,
            authorityId: undefined,
            authorityEpoch: undefined,
            conflict: false,
          }
        : current,
    );
  }

  function begin(operation: Mutation) {
    if (busy || replayRequired) return;
    setPending(operation);
    setConfirmationOpen(true);
    setError("");
  }

  function closeConfirmation(open: boolean) {
    if (busy) return;
    setConfirmationOpen(open);
    if (!open && !replayRequired) setPending(null);
  }

  async function submit() {
    if (!pending || busy || !sameOwner()) return;
    const collecting = pending.kind === "add" || pending.kind === "correct";
    if (collecting && (!enabled || !pending.content.trim())) return;
    const revision = pending.baseRevision ?? page?.revision;
    if (revision == null) return;
    const operation = {
      ...pending,
      operationId: pending.operationId ?? crypto.randomUUID(),
      baseRevision: revision,
      authorityId: pending.baseRevision === undefined ? page?.authority_id : pending.authorityId,
      authorityEpoch: pending.baseRevision === undefined ? page?.authority_epoch : pending.authorityEpoch,
    };
    setPending(operation);
    setBusy(true);
    setError("");
    try {
      const token = {
        operation_id: operation.operationId,
        base_revision: revision,
        authority_id: operation.authorityId,
        authority_epoch: operation.authorityEpoch,
      };
      const receipt =
        operation.kind === "add"
          ? await memoryApi.addRecord({
              ...token,
              target: operation.target,
              content: operation.content.trim(),
            })
          : operation.kind === "correct" && operation.record
            ? await memoryApi.correctRecord(operation.record.id, {
                ...token,
                content: operation.content.trim(),
              })
            : operation.kind === "forget" && operation.record
              ? await memoryApi.forgetRecord(operation.record.id, {
                  ...token,
                  target: operation.record.target,
                  project_id: operation.record.project_id,
                })
              : operation.source
                ? await memoryApi.forgetSource({
                    ...token,
                    kind: operation.source.kind,
                    source_id: operation.source.source_id,
                  })
                : null;
      if (!receipt || !sameOwner()) return;
      setNotice(receipt.context_notice);
      if (receipt.maintenance_complete === false) {
        setPending({ ...operation, uncertain: false, cleanupIncomplete: true });
        setError("cleanupIncomplete");
        setOffset(0);
        setRefresh((value) => value + 1);
        return;
      }
      setPending(null);
      setOffset(0);
      setRefresh((value) => value + 1);
    } catch (cause) {
      if (!sameOwner()) return;
      if (statusOf(cause) === 409) {
        setPage(null);
        try {
          const current = await memoryApi.listRecords(query);
          if (!sameOwner()) return;
          setPage(current);
          if (operation.uncertain || operation.cleanupIncomplete) {
            setPending(operation);
            setError("saveUncertain");
            return;
          }
          setPending({
            ...operation,
            operationId: undefined,
            baseRevision: undefined,
            authorityId: undefined,
            authorityEpoch: undefined,
            conflict: true,
          });
          setError("conflict");
        } catch {
          if (!sameOwner()) return;
          if (operation.uncertain || operation.cleanupIncomplete) {
            setPending(operation);
            setError("saveUncertain");
            return;
          }
          setPending({
            ...operation,
            operationId: undefined,
            baseRevision: undefined,
            authorityId: undefined,
            authorityEpoch: undefined,
            conflict: true,
          });
          setError("unavailable");
        }
      } else {
        const uncertain = operation.uncertain ||
          statusOf(cause) === undefined || (statusOf(cause) ?? 0) >= 500 ||
            codeOf(cause) === "memory.admission_recovery_required" ||
            codeOf(cause) === "MEMORY_WRITE_UNKNOWN";
        setPending({ ...operation, uncertain });
        setError(uncertain ? "saveUncertain" : "saveFailed");
      }
    } finally {
      if (sameOwner()) setBusy(false);
    }
  }

  const writing = pending?.kind === "add" || pending?.kind === "correct";
  const forgetting = pending?.kind === "forget" || pending?.kind === "source";
  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-meta">{label("contextLimit")}</p>
      {notice && (
        <p role="status" className="text-sm text-ink-body">
          {notice}
        </p>
      )}
      {displayedError && (!confirmationOpen || (!writing && !forgetting)) && (
        <p role="alert" className="text-sm text-error-text">
          {label(displayedError)}
        </p>
      )}
      {replayRequired && !confirmationOpen && (
        <Button variant="outline" disabled={busy} onClick={() => setConfirmationOpen(true)}>
          {t("common.retry")}
        </Button>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={scope}
          disabled={busy || replayRequired}
          onValueChange={(value) => {
            if (busy || replayRequired) return;
            setScope(value as typeof scope);
            setOffset(0);
          }}
        >
          <SelectTrigger size="sm" aria-label={label("scope")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{label("allScopes")}</SelectItem>
            <SelectItem value="user">{label("scopeUser")}</SelectItem>
            <SelectItem value="global">{label("scopeGlobal")}</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          disabled={loading || busy}
          onClick={() => setRefresh((value) => value + 1)}
        >
          {label("refreshRecords")}
        </Button>
        <Button
          disabled={!enabled || !page || busy || loading || replayRequired}
          onClick={() =>
            begin({ kind: "add", content: "", target: "user" })
          }
        >
          {label("addRecord")}
        </Button>
      </div>
      {loading ? (
        <LoadingState variant="section" />
      ) : page ? (
        <>
          {page.context_notice && (
            <p className="text-xs text-ink-meta">{page.context_notice}</p>
          )}
          <Card className="rounded-xl shadow-xs">
            <CardContent className="py-2">
              {page.records.length === 0 ? (
                <p className="py-8 text-center text-sm text-ink-meta">
                  {label("empty")}
                </p>
              ) : (
                page.records.map((record) => (
                  <div
                    key={record.id}
                    data-memory-record={record.id}
                    className="space-y-2 border-b border-surface-border py-3 last:border-b-0"
                  >
                    <p className="whitespace-pre-wrap text-sm text-ink-body">
                      {record.content}
                    </p>
                    <div className="flex flex-wrap gap-3 text-xs text-ink-meta">
                      <span>
                        {label(
                          record.target === "user"
                            ? "scopeUser"
                            : record.target === "global"
                              ? "scopeGlobal"
                              : "scopeProject",
                        )}
                      </span>
                      <span>
                        {label(record.confirmed ? "confirmed" : "unconfirmed")}
                      </span>
                      <time
                        dateTime={new Date(
                          record.observed_at * 1000,
                        ).toISOString()}
                      >
                        {new Date(record.observed_at * 1000).toLocaleString()}
                      </time>
                      <span>
                        {label("recordId")}: {record.id}
                      </span>
                    </div>
                    <ul className="space-y-2">
                      {record.source_refs.map((source) => (
                        <li
                          key={`${source.kind}:${source.source_id}:${source.revision ?? ""}:${source.origin}`}
                          className="flex flex-wrap items-center gap-2 text-xs text-ink-meta"
                        >
                          <span>
                            {label(`sourceKinds.${source.kind}`)} ·{" "}
                            {source.source_id} ·{" "}
                            {label(`sourceOrigins.${source.origin}`)}
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busy || loading || replayRequired}
                            onClick={() =>
                              begin({
                                kind: "source",
                                source,
                                content: "",
                                target: "user",
                              })
                            }
                          >
                            {label("forgetSource")}
                          </Button>
                        </li>
                      ))}
                    </ul>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!enabled || busy || loading || replayRequired}
                        onClick={() =>
                          begin({
                            kind: "correct",
                            record,
                            content: record.content,
                            target:
                              record.target === "global" ? "global" : "user",
                          })
                        }
                      >
                        {label("correctRecord")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy || loading || replayRequired}
                        onClick={() =>
                          begin({
                            kind: "forget",
                            record,
                            content: "",
                            target: "user",
                          })
                        }
                      >
                        {label("forgetRecord")}
                      </Button>
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              disabled={busy || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              {label("previousPage")}
            </Button>
            <span className="text-xs text-ink-meta">
              {page.total
                ? `${page.offset + 1}–${Math.min(page.offset + page.limit, page.total)}`
                : "0"}{" "}
              / {page.total}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || offset + page.limit >= page.total}
              onClick={() => setOffset(offset + page.limit)}
            >
              {label("nextPage")}
            </Button>
          </div>
        </>
      ) : null}
      <FormDialog
        open={!!writing && confirmationOpen}
        onOpenChange={closeConfirmation}
        title={label(pending?.kind === "add" ? "addRecord" : "correctRecord")}
        description={label("contextLimit")}
        loading={busy}
        submitLabel={pending?.uncertain ? t("common.retry") : label(pending?.conflict ? "confirmLatest" : "saveRecord")}
        cancelLabel={t("common.cancel")}
        onSubmit={() => void submit()}
      >
        {displayedError && (
          <p role="alert" className="text-sm text-error-text">
            {label(displayedError)}
          </p>
        )}
        {pending?.conflict && pending.record && (
          <p className="whitespace-pre-wrap text-sm text-ink-body">
            {label("latestRecordContent")}:{" "}
            {page?.records.find((record) => record.id === pending.record?.id)
              ?.content ?? label("recordRemoved")}
          </p>
        )}
        {pending?.kind === "add" && (
          <DialogField label={label("scope")} htmlFor="memory-target">
            <Select
              value={pending.target}
              disabled={busy || replayRequired}
              onValueChange={(value) => {
                if (busy || replayRequired) return;
                setPending({
                  ...pending,
                  target: value as "user" | "global",
                  operationId: undefined,
                  baseRevision: undefined,
                  authorityId: undefined,
                  authorityEpoch: undefined,
                });
              }}
            >
              <SelectTrigger size="sm" id="memory-target" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="user">{label("scopeUser")}</SelectItem>
                <SelectItem value="global">{label("scopeGlobal")}</SelectItem>
              </SelectContent>
            </Select>
          </DialogField>
        )}
        <DialogField label={label("recordContent")} htmlFor="memory-content">
          <Textarea
            id="memory-content"
            value={pending?.content ?? ""}
            disabled={busy || !enabled || replayRequired}
            onChange={(event) => editContent(event.target.value)}
          />
        </DialogField>
      </FormDialog>
      <DeleteConfirmDialog
        open={!!forgetting && confirmationOpen}
        onOpenChange={closeConfirmation}
        title={label(
          pending?.kind === "source" ? "forgetSource" : "forgetRecord",
        )}
        description={[
          displayedError ? label(displayedError) : "",
          label(
            pending?.kind === "source"
              ? "forgetSourceHint"
              : "forgetRecordHint",
          ),
          pending?.source
            ? `${label(`sourceKinds.${pending.source.kind}`)} · ${pending.source.source_id}`
            : pending?.record
              ? `${pending.record.content} · ${pending.record.id}`
              : "",
        ]
          .filter(Boolean)
          .join("\n")}
        itemName={pending?.source?.source_id ?? pending?.record?.content}
        confirmLabel={pending?.uncertain ? t("common.retry") : label(
          pending?.conflict ? "confirmLatest" : "confirmForget",
        )}
        cancelLabel={t("common.cancel")}
        loading={busy}
        onConfirm={() => void submit()}
      />
    </div>
  );
}

function statusOf(cause: unknown): number | undefined {
  return cause && typeof cause === "object" && "status" in cause
    ? Number(cause.status)
    : undefined;
}

function codeOf(cause: unknown): string | undefined {
  if (!cause || typeof cause !== "object" || !("body" in cause) || typeof cause.body !== "string") return;
  try {
    const body = JSON.parse(cause.body) as { detail?: { code?: string } };
    return body.detail?.code;
  } catch { return; }
}
