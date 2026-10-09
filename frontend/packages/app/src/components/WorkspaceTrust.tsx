/**
 * Workspace trust (H0) — the user decides whether a bound folder's own hook
 * commands (``.claude/settings.json``, Codex ``hooks.json`` …) may run.
 *
 * - ``WorkspaceTrustDialog``: asked once while binding a folder that carries
 *   such commands (``useProjectExecutionLocation`` → ``createProjectAt``).
 * - ``WorkspaceTrustSection``: the project context panel's view of the
 *   folder's commands and trust, with the switch.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  projectsApi,
  useTranslation,
  type WorkspaceHook,
  type WorkspaceTrustState,
} from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import { toast } from "sonner";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";

type TK = Parameters<ReturnType<typeof useTranslation>["t"]>[0];

function HookList({ hooks }: { hooks: WorkspaceHook[] }) {
  return (
    <ul className="max-h-60 space-y-2 overflow-y-auto">
      {hooks.map((hook, index) => (
        <li
          key={`${hook.source}:${hook.event}:${index}`}
          className="rounded-md border border-border bg-surface-soft px-3 py-2"
        >
          <div className="text-xs text-muted-foreground">
            {hook.source} · {hook.event}
            {hook.matcher ? ` · ${hook.matcher}` : ""}
          </div>
          <code className="mt-1 block break-all text-xs">{hook.command}</code>
        </li>
      ))}
    </ul>
  );
}

export function WorkspaceTrustDialog({
  hooks,
  onChoice,
}: {
  hooks: WorkspaceHook[] | null;
  /** true = trust, false = don't trust. Closing the dialog is "don't trust". */
  onChoice: (trusted: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={hooks !== null}
      onOpenChange={(open) => {
        if (!open) onChoice(false);
      }}
    >
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("project.trustDialogTitle" as TK)}</DialogTitle>
          <DialogDescription>
            {t("project.trustDialogDesc" as TK)}
          </DialogDescription>
        </DialogHeader>
        <HookList hooks={hooks ?? []} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onChoice(false)}>
            {t("project.trustDialogDistrust" as TK)}
          </Button>
          <Button onClick={() => onChoice(true)}>
            {t("project.trustDialogTrust" as TK)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Ask the trust question as a promise; render ``dialog`` somewhere.
 * ``ask(hooks)`` resolves with the user's answer.
 */
export function useWorkspaceTrustPrompt(): {
  ask: (hooks: WorkspaceHook[]) => Promise<boolean>;
  dialog: ReactNode;
} {
  const [pending, setPending] = useState<{
    hooks: WorkspaceHook[];
    resolve: (trusted: boolean) => void;
  } | null>(null);

  const ask = useCallback(
    (hooks: WorkspaceHook[]) =>
      new Promise<boolean>((resolve) => setPending({ hooks, resolve })),
    [],
  );

  const dialog = (
    <WorkspaceTrustDialog
      hooks={pending?.hooks ?? null}
      onChoice={(trusted) => {
        pending?.resolve(trusted);
        setPending(null);
      }}
    />
  );
  return { ask, dialog };
}

/**
 * The project's trust state, fetched once per project. ``null`` while loading
 * or when the backend does not know trust (older backend / remote target).
 */
export function useWorkspaceTrust(projectId: string | null): {
  state: WorkspaceTrustState | null;
  setTrusted: (trusted: boolean) => Promise<void>;
} {
  const [state, setState] = useState<WorkspaceTrustState | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState(null);
    if (!projectId) return;
    projectsApi
      .getWorkspaceTrust(projectId)
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch(() => {
        // Older backend or a target without trust: show nothing.
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const setTrusted = useCallback(
    async (trusted: boolean) => {
      if (!projectId) return;
      try {
        setState(await projectsApi.setWorkspaceTrust(projectId, trusted));
      } catch (error) {
        toast.error(
          _t("project.trustUpdateFailed" as Parameters<typeof _t>[0], {
            error: String(error),
          }),
        );
      }
    },
    [projectId],
  );
  return { state, setTrusted };
}

/** Whether the context panel should show the trust section at all. */
export function shouldShowWorkspaceTrust(
  state: WorkspaceTrustState | null,
): boolean {
  return (
    state !== null &&
    (state.hooks.length > 0 || state.workspace_trust === "untrusted")
  );
}

export function WorkspaceTrustSection({
  state,
  onSetTrusted,
}: {
  state: WorkspaceTrustState;
  onSetTrusted: (trusted: boolean) => void;
}) {
  const { t } = useTranslation();
  const trusted = state.workspace_trust === "trusted";
  return (
    <section
      className="space-y-2 px-3 py-3"
      data-testid="workspace-trust-section"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-foreground">
          {t("project.trustSectionTitle" as TK)}
        </h3>
        <Badge variant={trusted ? "success" : "warning"}>
          {trusted
            ? t("project.trustTrusted" as TK)
            : t("project.trustUntrusted" as TK)}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        {trusted
          ? t("project.trustTrustedHint" as TK)
          : t("project.trustUntrustedHint" as TK)}{" "}
        {t("project.trustAppliesToNewSessions" as TK)}
      </p>
      {state.hooks.length > 0 && <HookList hooks={state.hooks} />}
      <Button
        size="sm"
        variant="outline"
        onClick={() => onSetTrusted(!trusted)}
      >
        {trusted
          ? t("project.trustMakeUntrusted" as TK)
          : t("project.trustMakeTrusted" as TK)}
      </Button>
    </section>
  );
}
