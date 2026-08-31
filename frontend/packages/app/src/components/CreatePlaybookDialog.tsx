import { useEffect, useRef, useState } from "react";
import { Maximize2, Minimize2, Trash2 } from "lucide-react";
import {
  type AutomationProjectTarget,
  type PlaybookDefinition,
  type PlaybookDetail,
  type PlaybookStatus,
} from "@valuz/core";
import {
  Button,
  DeleteConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@valuz/ui";
import { useI18n } from "@valuz/ui";
import type { PlaybookTemplatePrefill } from "../lib/template-library";

export interface PlaybookAgentChoice {
  slug: string;
  name: string;
}

export interface CreatePlaybookDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: {
    name: string;
    content: string;
    project_id: string | null;
    status: PlaybookStatus;
    reference_metadata: Record<string, unknown>[];
    default_executor: Record<string, unknown>;
  }) => Promise<void>;
  onDelete?: (definition: PlaybookDefinition) => Promise<void>;
  initial?: PlaybookDetail | null;
  /** Create-mode defaults loaded from a market template. This never switches
   * the dialog into edit mode and does not persist anything until submit. */
  prefill?: PlaybookTemplatePrefill | null;
  targets: AutomationProjectTarget[];
  /** Library agents for Chat plus project-member projections keyed by
   * ``project_id``. This mirrors Automation's target-linked agent picker. */
  agents: PlaybookAgentChoice[];
  agentsByProject?: Record<string, PlaybookAgentChoice[]>;
  /**
   * Lock the Playbook to the project page that opened the dialog. The product
   * calls this a workspace in Finance, but persistence deliberately remains
   * ``project_id``. This mirrors CreateAutomationDialog's fixed-project mode:
   * the association stays visible and cannot be changed accidentally.
   */
  fixedProjectId?: string;
  fixedProjectName?: string;
}

export const CreatePlaybookDialog = ({
  open,
  onOpenChange,
  onSubmit,
  onDelete,
  initial,
  prefill,
  targets,
  agents,
  agentsByProject = {},
  fixedProjectId,
  fixedProjectName,
}: CreatePlaybookDialogProps) => {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [projectId, setProjectId] = useState("chat-default");
  const [agentSlug, setAgentSlug] = useState("");
  const [status, setStatus] = useState<PlaybookStatus>("draft");
  const [selectedVersion, setSelectedVersion] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const contentBeforeExpanded = useRef("");

  useEffect(() => {
    if (!open) return;
    setName(initial?.definition.name ?? prefill?.name ?? "");
    setContent(initial?.current_version.content ?? prefill?.content ?? "");
    setStatus(initial?.definition.status ?? prefill?.status ?? "draft");
    setSelectedVersion(initial?.definition.current_version ?? 1);
    setProjectId(
      fixedProjectId ?? initial?.definition.project_id ?? "chat-default",
    );
    const executor = initial?.current_version.default_executor;
    setAgentSlug(
      typeof executor?.agent_slug === "string"
        ? executor.agent_slug
        : (prefill?.default_agent_slug ?? ""),
    );
    setExpanded(false);
    setDeleteOpen(false);
  }, [fixedProjectId, initial, open, prefill]);

  const availableAgents = fixedProjectId
    ? agents
    : projectId === "chat-default"
      ? agents
      : (agentsByProject[projectId] ?? []);
  const effectiveAgentSlug =
    agentSlug && availableAgents.some((agent) => agent.slug === agentSlug)
      ? agentSlug
      : (availableAgents[0]?.slug ?? "");

  const versions =
    initial?.versions ?? (initial ? [initial.current_version] : []);
  const selectedVersionRecord = versions.find(
    (version) => version.version === selectedVersion,
  );

  const submit = async () => {
    if (
      !name.trim() ||
      !content.trim() ||
      !effectiveAgentSlug ||
      submitting
    )
      return;
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        content: content.trim(),
        project_id:
          fixedProjectId ?? (projectId === "chat-default" ? null : projectId),
        status,
        reference_metadata: selectedVersionRecord?.reference_metadata ?? [],
        default_executor: { agent_slug: effectiveAgentSlug },
      });
      onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  const useVersion = (value: string) => {
    const version = versions.find((item) => item.version === Number(value));
    if (!version) return;
    setSelectedVersion(version.version);
    setContent(version.content);
    const executorAgent = version.default_executor.agent_slug;
    setAgentSlug(typeof executorAgent === "string" ? executorAgent : "");
  };

  const confirmDelete = async () => {
    if (!initial || !onDelete || deleting) return;
    setDeleting(true);
    try {
      await onDelete(initial.definition);
      setDeleteOpen(false);
      onOpenChange(false);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="h-[min(640px,85vh)] max-w-xl gap-0 overflow-hidden p-0">
        {expanded ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <DialogHeader className="px-[18px] pt-[18px] pb-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <DialogTitle className="text-sm leading-5">
                    {t("playbook.promptLabel")}
                  </DialogTitle>
                  <DialogDescription className="sr-only">
                    {t("playbook.promptHint")}
                  </DialogDescription>
                </div>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={t("playbook.collapseEditor")}
                  onClick={() => setExpanded(false)}
                >
                  <Minimize2 className="h-4 w-4" />
                </Button>
              </div>
            </DialogHeader>
            <div className="flex min-h-0 flex-1 flex-col px-[18px]">
              <Textarea
                autoFocus
                value={content}
                onChange={(event) => setContent(event.target.value)}
                className="min-h-0 max-h-none flex-1 resize-none field-sizing-fixed font-mono text-sm"
                placeholder={t("playbook.promptPlaceholder")}
              />
            </div>
            <DialogFooter className="px-[18px] pt-3 pb-4">
              <Button
                variant="outline"
                onClick={() => {
                  setContent(contentBeforeExpanded.current);
                  setExpanded(false);
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button onClick={() => setExpanded(false)}>
                {t("common.done")}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            <DialogHeader className="px-[18px] pt-[18px] pb-1">
              <DialogTitle className="text-sm leading-5">
                {initial ? t("playbook.editTitle") : t("playbook.createTitle")}
              </DialogTitle>
              <DialogDescription>{t("playbook.dialogHint")}</DialogDescription>
            </DialogHeader>
            <div className="flex min-h-0 flex-1 flex-col gap-[14px] overflow-y-auto px-[18px] py-[14px]">
              <div className="grid grid-cols-[minmax(0,1fr)_160px] items-start gap-2">
                <FormField label={t("playbook.nameLabel")}>
                  <Input
                    autoFocus
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={t("playbook.namePlaceholder")}
                  />
                </FormField>
                <FormField label={t("playbook.statusLabel")}>
                  <Select
                    value={status}
                    onValueChange={(value) => setStatus(value as PlaybookStatus)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(["draft", "active", "retired"] as const).map(
                        (value) => (
                          <SelectItem key={value} value={value}>
                            {t(`playbook.status.${value}`)}
                          </SelectItem>
                        ),
                      )}
                    </SelectContent>
                  </Select>
                </FormField>
              </div>

              <div className="grid grid-cols-2 items-start gap-2">
                <FormField label={t("automation.targetLabelTask")}>
                  {fixedProjectId ? (
                    <Select value={fixedProjectId} disabled>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={fixedProjectId}>
                          {fixedProjectName ?? fixedProjectId}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  ) : (
                    <Select value={projectId} onValueChange={setProjectId}>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {targets.map((target) => (
                          <SelectItem key={target.id} value={target.id}>
                            {target.kind === "chat"
                              ? t("automation.targetChat")
                              : target.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </FormField>

                {/* The reusable Definition stores only a default Agent. Its
                    execution location is still chosen for each PlaybookRun. */}
                <FormField label={t("automation.agentLabel")}>
                  <Select
                    value={effectiveAgentSlug}
                    onValueChange={setAgentSlug}
                    disabled={availableAgents.length === 0}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue
                        placeholder={
                          availableAgents.length === 0
                            ? t("automation.agentPlaceholderEmpty")
                            : t("automation.agentPlaceholderPick")
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {availableAgents.map((agent) => (
                        <SelectItem key={agent.slug} value={agent.slug}>
                          {agent.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormField>
              </div>

              <FormField
                className="min-h-0 flex-1"
                label={t("playbook.promptLabel")}
                labelAction={
                  <div className="flex items-center gap-1.5">
                    {initial ? (
                      <Select
                        value={String(selectedVersion)}
                        onValueChange={useVersion}
                      >
                        <SelectTrigger
                          className="h-5 w-auto min-w-[92px] border-0 bg-transparent px-1.5 text-[11px] text-ink-meta shadow-none"
                          aria-label={t("playbook.versionHistoryLabel")}
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent align="end">
                          {versions.map((version) => (
                            <SelectItem
                              key={version.version}
                              value={String(version.version)}
                            >
                              {t("playbook.versionOption", {
                                version: version.version,
                                current:
                                  version.version ===
                                  initial.definition.current_version
                                    ? t("playbook.versionCurrent")
                                    : "",
                              })}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                    <button
                      type="button"
                      className="flex h-5 w-5 items-center justify-center rounded text-ink-meta transition-colors hover:bg-surface-muted hover:text-ink-body"
                      title={t("playbook.expandEditor")}
                      aria-label={t("playbook.expandEditor")}
                      onClick={() => {
                        contentBeforeExpanded.current = content;
                        setExpanded(true);
                      }}
                    >
                      <Maximize2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                }
              >
                <div className="flex min-h-0 flex-1 flex-col gap-1.5">
                  <Textarea
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    className="min-h-0 max-h-none flex-1 resize-none field-sizing-fixed font-mono text-sm"
                    placeholder={t("playbook.promptPlaceholder")}
                  />
                  {initial ? (
                    <p className="text-[11px] leading-4 text-ink-meta">
                      {selectedVersion === initial.definition.current_version
                        ? t("playbook.versionSaveHint", {
                            version: initial.definition.current_version + 1,
                          })
                        : t("playbook.versionReuseHint", {
                            source: selectedVersion,
                            version: initial.definition.current_version + 1,
                          })}
                    </p>
                  ) : null}
                </div>
              </FormField>
            </div>
            <DialogFooter className="px-[18px] pt-1 pb-4">
              {initial && onDelete ? (
                <Button
                  variant="ghost"
                  className="mr-auto text-error-text hover:bg-error-light hover:text-error-text"
                  onClick={() => setDeleteOpen(true)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {t("common.delete")}
                </Button>
              ) : null}
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t("common.cancel")}
              </Button>
              <Button
                disabled={
                  !name.trim() ||
                  !content.trim() ||
                  !effectiveAgentSlug ||
                  submitting
                }
                onClick={() => void submit()}
              >
                {submitting ? t("common.processing") : t("common.save")}
              </Button>
            </DialogFooter>
          </>
        )}
        </DialogContent>
      </Dialog>
      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t("playbook.deleteTitle", {
          name: initial?.definition.name ?? "",
        })}
        description={t("playbook.deleteDescription")}
        confirmLabel={t("common.delete")}
        loading={deleting}
        onConfirm={() => void confirmDelete()}
      />
    </>
  );
};
