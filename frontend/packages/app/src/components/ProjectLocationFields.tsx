/**
 * Execution-location support for the two create-project dialogs
 * (``ProjectLayoutBase`` sidebar dialog + ``ProjectsPage`` dialog).
 *
 * Single-target builds (OSS) register no execution targets: the hook resolves
 * to no target, ``ProjectLocationFields`` renders nothing, and
 * ``createProjectAt`` degrades to a plain ``projectsApi.create`` — zero
 * behaviour change.
 *
 * On a multi-target edition, picking a ``remote`` target (cloud) switches the
 * dialog from "bind a local directory" to "managed cwd + optional
 * initial-content upload": the project is created on the remote backend with
 * no ``root_path`` (the backend allocates a managed cwd), and a locally
 * picked folder is streamed up via the multipart
 * ``POST /v1/projects/{id}/files`` endpoint in batches.
 *
 * A remote target that can browse ITS OWN filesystem (a remote desktop
 * reached through the relay) provides ``ExecutionTarget.selectDirectory``;
 * the dialogs then keep the directory field and call that chooser instead of
 * the platform's native picker, and the project binds a directory on the
 * remote machine. When the chooser reports the directory is already a project
 * there, the dialog opens that project instead of creating a duplicate.
 */

import { useCallback, useState } from "react";
import {
  getDefaultExecutionTarget,
  projectsApi,
  recordEntityOrigin,
  targetUsesManagedCwd,
  useExecutionTargets,
  useTranslation,
  type ExecutionTarget,
  type ExecutionTargetDirectory,
  type ProjectDetail,
} from "@valuz/core";
import { Button } from "@valuz/ui";
import { FolderUp, X } from "lucide-react";
import { usePlatform } from "../platform";
import { ExecutionLocationPicker } from "./ExecutionLocationPicker";

type TK = Parameters<ReturnType<typeof useTranslation>["t"]>[0];

/** Directory names never worth shipping to a fresh remote cwd. */
const UPLOAD_EXCLUDED_SEGMENTS = new Set([
  "node_modules",
  ".git",
  ".venv",
  "venv",
  "__pycache__",
  ".DS_Store",
  "dist",
  "build",
  ".next",
  ".turbo",
  ".idea",
]);

const MAX_UPLOAD_FILES = 2000;
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024; // 100 MB
const UPLOAD_BATCH_SIZE = 20;

/** ``webkitRelativePath`` minus the picked folder's own name, so the folder
 * CONTENTS land at the project root — same semantics as binding the folder
 * as a local project cwd. */
function relativeUploadPath(file: File): string {
  const rel = file.webkitRelativePath || file.name;
  const cut = rel.indexOf("/");
  return cut === -1 ? rel : rel.slice(cut + 1);
}

function isExcluded(file: File): boolean {
  const rel = file.webkitRelativePath || file.name;
  return rel
    .split("/")
    .some((segment) => UPLOAD_EXCLUDED_SEGMENTS.has(segment));
}

export interface ProjectExecutionLocation {
  /** Registered execution targets ([] on single-target builds). */
  targets: ExecutionTarget[];
  targetId: string | null;
  setTargetId: (id: string) => void;
  /** The target creation will use (undefined on single-target builds). */
  effectiveTarget: ExecutionTarget | undefined;
  /**
   * True when creation must use the managed cwd + upload flow: the target
   * is remote and offers no directory chooser of its own.
   */
  isRemoteTarget: boolean;
  /**
   * Pick a directory FOR the effective target: its own chooser when it has
   * one (remote desktop), else the platform's native picker. ``null`` when
   * cancelled / unavailable.
   */
  selectDirectory: () => Promise<ExecutionTargetDirectory | null>;
  /** Files picked for the remote initial-content upload (post-filter). */
  initialFiles: File[];
  pickInitialFolder: () => void;
  clearInitialFiles: () => void;
  /** Filter/caps message for the last pick, or "" when it was accepted. */
  pickError: string;
  /**
   * Create the project on the chosen target and record its origin. On a
   * remote target ``root_path`` is dropped (managed cwd).
   */
  createProjectAt: (payload: {
    name: string;
    root_path?: string;
  }) => Promise<ProjectDetail>;
  /** Batched multipart upload of the picked folder. Throws on failure. */
  uploadInitialFiles: (projectId: string) => Promise<number>;
  reset: () => void;
}

export function useProjectExecutionLocation(): ProjectExecutionLocation {
  const { t } = useTranslation();
  const platform = usePlatform();
  const targets = useExecutionTargets();
  const [targetId, setTargetId] = useState<string | null>(null);
  const [initialFiles, setInitialFiles] = useState<File[]>([]);
  const [pickError, setPickError] = useState("");

  const effectiveTarget =
    targets.length === 0
      ? undefined
      : (targets.find((target) => target.id === targetId) ??
        getDefaultExecutionTarget());
  const isRemoteTarget = targetUsesManagedCwd(effectiveTarget);

  const selectDirectory = useCallback(async () => {
    const own = effectiveTarget?.selectDirectory;
    if (own) return await own();
    const path = await platform.selectDirectory();
    return path ? { path } : null;
  }, [effectiveTarget, platform]);

  const pickInitialFolder = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    // Chromium-only attribute (Electron + the browsers we target); TS DOM
    // libs type it on HTMLInputElement.
    input.webkitdirectory = true;
    input.onchange = () => {
      const all = input.files ? Array.from(input.files) : [];
      const kept = all.filter((file) => !isExcluded(file));
      const totalBytes = kept.reduce((sum, file) => sum + file.size, 0);
      if (kept.length > MAX_UPLOAD_FILES || totalBytes > MAX_UPLOAD_BYTES) {
        setInitialFiles([]);
        setPickError(
          t("project.initialFilesTooLarge" as TK, {
            maxFiles: String(MAX_UPLOAD_FILES),
            maxMb: String(Math.floor(MAX_UPLOAD_BYTES / 1024 / 1024)),
          }),
        );
        return;
      }
      setPickError("");
      setInitialFiles(kept);
    };
    input.click();
  }, [t]);

  const clearInitialFiles = useCallback(() => {
    setInitialFiles([]);
    setPickError("");
  }, []);

  const createProjectAt = useCallback(
    async (payload: { name: string; root_path?: string }) => {
      const target = effectiveTarget;
      const body = targetUsesManagedCwd(target) ? { name: payload.name } : payload;
      const created = await projectsApi.create(
        body,
        target ? { baseUrl: target.baseUrl } : undefined,
      );
      // Record BEFORE any follow-up call so agent deploys / uploads /
      // detail fetches for this project route to the owning backend.
      if (target) recordEntityOrigin(created.id, target.id);
      return created;
    },
    [effectiveTarget],
  );

  const uploadInitialFiles = useCallback(
    async (projectId: string) => {
      if (initialFiles.length === 0) return 0;
      for (let i = 0; i < initialFiles.length; i += UPLOAD_BATCH_SIZE) {
        const batch = initialFiles
          .slice(i, i + UPLOAD_BATCH_SIZE)
          // Re-wrap so the multipart filename carries the RELATIVE path —
          // the backend writes each file to that path under the cwd.
          .map(
            (file) =>
              new File([file], relativeUploadPath(file), { type: file.type }),
          );
        await projectsApi.uploadFiles(projectId, batch);
      }
      return initialFiles.length;
    },
    [initialFiles],
  );

  const reset = useCallback(() => {
    setTargetId(null);
    setInitialFiles([]);
    setPickError("");
  }, []);

  return {
    targets,
    targetId,
    setTargetId,
    effectiveTarget,
    isRemoteTarget,
    selectDirectory,
    initialFiles,
    pickInitialFolder,
    clearInitialFiles,
    pickError,
    createProjectAt,
    uploadInitialFiles,
    reset,
  };
}

/**
 * Dialog fields: the location segmented control and — for a remote target —
 * the optional initial-folder pick row. Renders nothing on single-target
 * builds. The caller keeps rendering its own directory field when
 * ``isRemoteTarget`` is false.
 */
export function ProjectLocationFields({
  state,
}: {
  state: ProjectExecutionLocation;
}) {
  const { t } = useTranslation();
  if (state.targets.length < 2) return null;
  return (
    <>
      <div className="flex flex-col">
        <label className="mb-[5px] text-xs font-medium text-foreground">
          {t("project.execLocation" as TK)}
        </label>
        <ExecutionLocationPicker
          value={state.targetId}
          onChange={state.setTargetId}
        />
      </div>
      {state.isRemoteTarget ? (
        <div className="flex flex-col">
          <label className="mb-[5px] text-xs font-medium text-foreground">
            {t("project.initialFiles" as TK)}
          </label>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 shrink-0"
              onClick={state.pickInitialFolder}
            >
              <FolderUp className="mr-1.5 h-4 w-4" />
              {t("project.pickFolder" as TK)}
            </Button>
            {state.initialFiles.length > 0 ? (
              <span className="flex min-w-0 items-center gap-1 text-xs text-ink-meta">
                <span className="truncate">
                  {t("project.initialFilesPicked" as TK, {
                    count: String(state.initialFiles.length),
                  })}
                </span>
                <button
                  type="button"
                  aria-label={t("common.delete" as TK)}
                  className="shrink-0 rounded p-0.5 text-ink-muted hover:bg-surface-soft hover:text-ink-body"
                  onClick={state.clearInitialFiles}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ) : null}
          </div>
          <p className="mt-[3px] text-xs text-muted-foreground">
            {t("project.initialFilesHint" as TK)}
          </p>
          {state.pickError ? (
            <p className="mt-[3px] text-xs text-destructive">
              {state.pickError}
            </p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
