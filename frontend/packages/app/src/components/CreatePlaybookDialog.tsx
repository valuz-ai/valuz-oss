import { useEffect, useRef, useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import {
  getDefaultExecutionTarget,
  useExecutionTargets,
  type AutomationProjectTarget,
  type PlaybookDetail,
} from "@valuz/core";
import {
  Button,
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
import {
  ExecutionLocationPicker,
  OriginBadge,
} from "./ExecutionLocationPicker";

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
    default_executor: Record<string, unknown>;
    /** Creation target for a global Playbook; never persisted as domain data. */
    exec_location?: string;
  }) => Promise<void>;
  initial?: PlaybookDetail | null;
  targets: AutomationProjectTarget[];
  agents: PlaybookAgentChoice[];
}

export const CreatePlaybookDialog = ({
  open,
  onOpenChange,
  onSubmit,
  initial,
  targets,
  agents,
}: CreatePlaybookDialogProps) => {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [projectId, setProjectId] = useState("__global__");
  const [agentSlug, setAgentSlug] = useState("__default__");
  const [execLocation, setExecLocation] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const contentBeforeExpanded = useRef("");
  const executionTargets = useExecutionTargets();

  useEffect(() => {
    if (!open) return;
    setName(initial?.definition.name ?? "");
    setContent(initial?.current_version.content ?? "");
    setProjectId(initial?.definition.project_id ?? "__global__");
    const executor = initial?.current_version.default_executor;
    setAgentSlug(
      typeof executor?.agent_slug === "string"
        ? executor.agent_slug
        : "__default__",
    );
    setExecLocation(
      initial?.definition.exec_origin ?? getDefaultExecutionTarget()?.id ?? null,
    );
    setExpanded(false);
  }, [initial, open]);

  const submit = async () => {
    if (!name.trim() || !content.trim() || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        content: content.trim(),
        project_id: projectId === "__global__" ? null : projectId,
        default_executor:
          agentSlug === "__default__" ? {} : { agent_slug: agentSlug },
        exec_location:
          !initial && projectId === "__global__"
            ? execLocation ?? undefined
            : undefined,
      });
      onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={
          expanded
            ? "h-5/6 max-w-4xl gap-0 overflow-hidden p-0"
            : "h-3/4 max-w-2xl gap-0 overflow-hidden p-0"
        }
      >
        {expanded ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <DialogHeader className="border-b border-surface-border p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <DialogTitle>{t("playbook.promptLabel")}</DialogTitle>
                  <DialogDescription>
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
            <div className="flex min-h-0 flex-1 p-4">
              <Textarea
                autoFocus
                value={content}
                onChange={(event) => setContent(event.target.value)}
                className="min-h-0 max-h-none flex-1 resize-none field-sizing-fixed font-mono text-sm"
                placeholder={t("playbook.promptPlaceholder")}
              />
            </div>
            <DialogFooter className="border-t border-surface-border p-4">
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
            <DialogHeader className="border-b border-surface-border p-4">
              <DialogTitle>
                {initial ? t("playbook.editTitle") : t("playbook.createTitle")}
              </DialogTitle>
              <DialogDescription>{t("playbook.dialogHint")}</DialogDescription>
            </DialogHeader>
            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
              <FormField label={t("playbook.nameLabel")}>
                <Input
                  autoFocus
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={t("playbook.namePlaceholder")}
                />
              </FormField>

              <FormField label={t("playbook.projectLabel")}>
                <Select value={projectId} onValueChange={setProjectId}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__global__">
                      {t("playbook.projectGlobal")}
                    </SelectItem>
                    {targets
                      .filter(
                        (target) => target.kind === "project" && target.project_id,
                      )
                      .map((target) => (
                        <SelectItem
                          key={target.project_id!}
                          value={target.project_id!}
                        >
                          {target.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </FormField>

              {executionTargets.length >= 2 ? (
                <FormField
                  label={t(
                    "project.execLocation" as Parameters<typeof t>[0],
                  )}
                >
                  {!initial && projectId === "__global__" ? (
                    <ExecutionLocationPicker
                      value={execLocation}
                      onChange={setExecLocation}
                    />
                  ) : (
                    <OriginBadge
                      origin={initial?.definition.exec_origin}
                      entityId={
                        initial?.definition.id ??
                        (projectId === "__global__" ? null : projectId)
                      }
                      kind={initial ? "playbook" : "project"}
                    />
                  )}
                </FormField>
              ) : null}

              <FormField label={t("playbook.executorLabel")}>
                <Select value={agentSlug} onValueChange={setAgentSlug}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__default__">
                      {t("playbook.executorDefault")}
                    </SelectItem>
                    {agents.map((agent) => (
                      <SelectItem key={agent.slug} value={agent.slug}>
                        {agent.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormField>

              <FormField label={t("playbook.promptLabel")}>
                <div className="relative">
                  <Textarea
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    className="min-h-48 resize-y pr-10 font-mono text-sm"
                    placeholder={t("playbook.promptPlaceholder")}
                  />
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="absolute right-1 top-1"
                    aria-label={t("playbook.expandEditor")}
                    onClick={() => {
                      contentBeforeExpanded.current = content;
                      setExpanded(true);
                    }}
                  >
                    <Maximize2 className="h-4 w-4" />
                  </Button>
                </div>
                <p className="mt-1 text-2xs leading-4 text-ink-meta">
                  {t("playbook.promptHint")}
                </p>
              </FormField>
            </div>
            <DialogFooter className="border-t border-surface-border p-4">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t("common.cancel")}
              </Button>
              <Button
                disabled={!name.trim() || !content.trim() || submitting}
                onClick={() => void submit()}
              >
                {submitting ? t("common.processing") : t("common.save")}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
};
