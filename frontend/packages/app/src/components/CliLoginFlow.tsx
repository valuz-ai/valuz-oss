import { useCallback, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  Button,
} from "@valuz/ui";
import { t } from "@valuz/shared/i18n";
import { providersApi } from "@valuz/core";
import { usePlatform } from "../platform/context";

export type CliTool = "claude" | "codex";

export type CliLoginState = "logged_in" | "logged_out" | "unsupported";

export interface CliLoginStatus {
  installed: boolean;
  state: CliLoginState;
  cliPath: string | null;
}

interface CliLoginLaunchResult {
  launched: boolean;
  error?: string;
}

const TOOL_LABEL: Record<CliTool, string> = {
  claude: "Claude",
  codex: "Codex",
};

const INSTALL_COMMAND: Record<CliTool, string> = {
  claude: "npm install -g @anthropic-ai/claude-code",
  codex: "npm install -g @openai/codex",
};

/** Map a CLI tool to its seeded subscription provider id. */
const SUBSCRIPTION_PROVIDER_ID: Record<CliTool, string> = {
  claude: "ch-claude-subscription",
  codex: "ch-codex-subscription",
};

/**
 * Enable the CLI subscription provider after a successful login. The host
 * can't see the CLI keychain, so we tell it the login succeeded and it flips
 * the provider to enabled + ``credential_source="cli_keychain"``. Returns true
 * when the provider became (or already was) usable.
 */
const markProviderAsOAuth = async (tool: CliTool): Promise<boolean> => {
  try {
    await providersApi.enable(SUBSCRIPTION_PROVIDER_ID[tool]);
    return true;
  } catch {
    // Non-fatal: the runtime still reads the CLI keychain directly. A failed
    // enable just means the provider list won't reflect it yet.
    return false;
  }
};

const LOGIN_COMMAND_LABEL: Record<CliTool, string> = {
  claude: "claude /login",
  codex: "codex login",
};

const launchAndToast = async (
  tool: CliTool,
  launchCliLogin: (tool: string) => Promise<unknown>,
) => {
  const result = (await launchCliLogin(tool)) as CliLoginLaunchResult;
  if (result.launched) {
    toast.success(
      t("cliLogin.terminalStarted" as Parameters<typeof t>[0], {
        tool: LOGIN_COMMAND_LABEL[tool],
      }),
    );
    return;
  }
  if (result.error === "no_terminal") {
    toast.error(t("cliLogin.noTerminalEmulator" as Parameters<typeof t>[0]));
    return;
  }
  if (result.error === "unsupported_platform") {
    toast.error(t("cliLogin.notSupported" as Parameters<typeof t>[0]));
    return;
  }
  toast.error(
    result.error ?? t("cliLogin.terminalFailed" as Parameters<typeof t>[0]),
  );
};

interface DialogState {
  kind: "missing" | "already_logged_in";
  tool: CliTool;
}

export interface CliLoginFlowOptions {
  onAlreadyLoggedIn?: (tool: CliTool) => void;
  onProviderMarkedOAuth?: (tool: CliTool) => void;
}

export interface CliLoginFlowApi {
  trigger: (tool: CliTool) => Promise<void>;
  dialog: ReactNode;
}

export const useCliLoginFlow = (
  options: CliLoginFlowOptions = {},
): CliLoginFlowApi => {
  const [dialogState, setDialogState] = useState<DialogState | null>(null);
  const { onAlreadyLoggedIn, onProviderMarkedOAuth } = options;
  const platform = usePlatform();

  const syncProviderOAuth = useCallback(
    async (tool: CliTool) => {
      const updated = await markProviderAsOAuth(tool);
      if (updated) onProviderMarkedOAuth?.(tool);
    },
    [onProviderMarkedOAuth],
  );

  const trigger = useCallback(
    async (tool: CliTool) => {
      if (!platform.isElectron) {
        toast.info(t("cliLogin.desktopOnly" as Parameters<typeof t>[0]));
        return;
      }
      const status = (await platform.checkCliLogin?.(tool)) as
        | CliLoginStatus
        | undefined;
      if (!status || status.state === "unsupported") {
        toast.error(t("cliLogin.notSupported" as Parameters<typeof t>[0]));
        return;
      }
      if (!status.installed) {
        setDialogState({ kind: "missing", tool });
        return;
      }
      if (status.state === "logged_in") {
        await syncProviderOAuth(tool);
        setDialogState({ kind: "already_logged_in", tool });
        return;
      }
      await launchAndToast(tool, platform.launchCliLogin!);
      await syncProviderOAuth(tool);
    },
    [syncProviderOAuth, platform],
  );

  const closeDialog = useCallback(() => setDialogState(null), []);

  const dialog =
    dialogState !== null ? (
      <AlertDialog
        open
        onOpenChange={(open) => {
          if (!open) closeDialog();
        }}
      >
        <AlertDialogContent>
          {dialogState.kind === "missing" ? (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {t("cliLogin.notDetected" as Parameters<typeof t>[0], {
                    tool: TOOL_LABEL[dialogState.tool],
                  })}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {t("cliLogin.installHint" as Parameters<typeof t>[0])}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <div className="rounded-md border border-surface-border bg-surface-soft px-3 py-2 font-mono text-xs text-ink-heading">
                {INSTALL_COMMAND[dialogState.tool]}
              </div>
              <AlertDialogFooter>
                <AlertDialogCancel>
                  {t("common.close" as Parameters<typeof t>[0])}
                </AlertDialogCancel>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(
                        INSTALL_COMMAND[dialogState.tool],
                      );
                      toast.success(
                        t("cliLogin.copied" as Parameters<typeof t>[0]),
                      );
                    } catch {
                      toast.error(
                        t("cliLogin.copyFailed" as Parameters<typeof t>[0]),
                      );
                    }
                  }}
                >
                  {t("cliLogin.copyCommand" as Parameters<typeof t>[0])}
                </Button>
              </AlertDialogFooter>
            </>
          ) : (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {t("cliLogin.loggedIn" as Parameters<typeof t>[0], {
                    tool: TOOL_LABEL[dialogState.tool],
                  })}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {t(
                    "cliLogin.alreadyLoggedInDesc" as Parameters<typeof t>[0],
                    { tool: TOOL_LABEL[dialogState.tool] },
                  )}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel
                  onClick={() => {
                    closeDialog();
                    onAlreadyLoggedIn?.(dialogState.tool);
                  }}
                >
                  {t("cliLogin.keepLogin" as Parameters<typeof t>[0])}
                </AlertDialogCancel>
                <AlertDialogAction
                  onClick={async () => {
                    const tool = dialogState.tool;
                    closeDialog();
                    await launchAndToast(tool, platform.launchCliLogin!);
                    await syncProviderOAuth(tool);
                  }}
                >
                  {t("cliLogin.relogin" as Parameters<typeof t>[0])}
                </AlertDialogAction>
              </AlertDialogFooter>
            </>
          )}
        </AlertDialogContent>
      </AlertDialog>
    ) : null;

  return { trigger, dialog };
};
