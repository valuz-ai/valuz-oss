import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  FileText,
  Eye,
  FilePenLine,
  Loader2,
  Check,
  X,
  Bot,
  ExternalLink,
} from "lucide-react";
import {
  Badge,
  Button,
  Textarea,
  BackLink,
  Input,
  MarkdownContent,
} from "@valuz/ui";
import { skillsApi } from "@valuz/core";
import type { SkillDetail } from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import { toast } from "sonner";
import { useTranslation } from "@valuz/core";

type TreeNode = {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: TreeNode[];
};

function isBinaryContent(content: string): boolean {
  // Heuristic: if content has null bytes or very few printable characters relative to length, treat as binary
  if (content.includes("\x00")) return true;
  const printable = content
    .split("")
    .filter((c) => c.charCodeAt(0) >= 32 && c.charCodeAt(0) < 127).length;
  return content.length > 0 && printable / content.length < 0.7;
}

function findFirstFile(nodes: TreeNode[]): TreeNode | null {
  for (const n of nodes) {
    if (n.type === "file") return n;
    if (n.children) {
      const found = findFirstFile(n.children);
      if (found) return found;
    }
  }
  return null;
}

export const SkillDetailPage = () => {
  const { t } = useTranslation();
  const { skillId = "" } = useParams();
  const navigate = useNavigate();
  const { setHeader } = useProjectOutlet();

  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [files, setFiles] = useState<TreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [viewMode, setViewMode] = useState<"preview" | "source" | "edit">(
    "preview",
  );
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Metadata editing state
  const [metaEditing, setMetaEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editTags, setEditTags] = useState("");
  const [metaSaving, setMetaSaving] = useState(false);

  const decodedId = decodeURIComponent(skillId);

  const isMarkdownFile = selectedFile?.endsWith(".md") ?? false;

  const loadSkill = useCallback(async () => {
    setLoading(true);
    try {
      const [detail, fileList] = await Promise.all([
        skillsApi.get(decodedId),
        skillsApi.listFiles(decodedId),
      ]);
      setSkill(detail);
      setEditName(detail.name);
      setEditDesc(detail.description);
      setEditTags(detail.tags?.join(", ") ?? "");
      setFiles(fileList as unknown as TreeNode[]);
      const firstFile = findFirstFile(fileList as unknown as TreeNode[]);
      if (firstFile) {
        setSelectedFile(firstFile.path);
      }
    } catch (err) {
      console.error("[SkillDetail] load error", err);
    } finally {
      setLoading(false);
    }
  }, [decodedId]);

  useEffect(() => {
    loadSkill();
  }, [loadSkill]);

  useEffect(() => {
    if (!selectedFile || !decodedId) return;
    skillsApi
      .getFileContent(decodedId, selectedFile)
      .then((res) => {
        const binary = isBinaryContent(res.content);
        setFileContent(binary ? "[Binary file - cannot preview]" : res.content);
        setEditContent(res.content);
      })
      .catch(() => setFileContent(""));
  }, [selectedFile, decodedId]);

  useEffect(() => {
    const name = skill?.name || decodedId;
    setHeader(
      <div className="flex items-center gap-3">
        <BackLink
          onClick={() => navigate("/skills")}
          label={t("common.back" as Parameters<typeof t>[0])}
        />
        <Badge variant="outline" className="text-2xs">
          Skill
        </Badge>
        <span className="text-base font-medium text-ink-heading">{name}</span>
      </div>,
    );
    return () => setHeader(null);
  }, [skill, decodedId, navigate, setHeader]);

  const handleSave = useCallback(async () => {
    if (!selectedFile || !decodedId) return;
    setSaving(true);
    try {
      await skillsApi.updateFile(decodedId, {
        action: "create",
        path: selectedFile,
        content: editContent,
      });
      setFileContent(editContent);
      setViewMode("preview");
      toast.success(t("skill.saved" as Parameters<typeof t>[0]));
    } catch (err) {
      console.error("[SkillDetail] save error", err);
      toast.error(t("common.saveFailed" as Parameters<typeof t>[0]));
    } finally {
      setSaving(false);
    }
  }, [decodedId, selectedFile, editContent]);

  const handleEditChange = (value: string) => {
    setEditContent(value);
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      if (decodedId && selectedFile) {
        skillsApi
          .updateFile(decodedId, {
            action: "create",
            path: selectedFile,
            content: value,
          })
          .catch(() => {});
      }
    }, 1000);
  };

  const handleMetaSave = async () => {
    if (!decodedId) return;
    setMetaSaving(true);
    try {
      const tags = editTags
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean);
      await skillsApi.update(decodedId, {
        name: editName.trim(),
        description: editDesc.trim(),
        tags,
      });
      setSkill((prev) =>
        prev
          ? {
              ...prev,
              name: editName.trim(),
              description: editDesc.trim(),
              tags,
            }
          : prev,
      );
      setMetaEditing(false);
      toast.success(t("skill.metadataUpdated" as Parameters<typeof t>[0]));
    } catch (err) {
      toast.error(
        t("skill.operationFailed" as Parameters<typeof t>[0], {
          error: err instanceof Error ? err.message : "unknown",
        }),
      );
    } finally {
      setMetaSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-brand" />
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Left sidebar: metadata + file tree */}
      <div className="w-[280px] shrink-0 border-r border-surface-border bg-surface-base">
        {/* Metadata card */}
        <div className="border-b border-surface-border px-4 py-3">
          {metaEditing ? (
            <div className="space-y-2">
              <Input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                placeholder={t(
                  "skill.namePlaceholder" as Parameters<typeof t>[0],
                )}
                className="h-7 text-xs"
              />
              <Textarea
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                placeholder={t("common.description" as Parameters<typeof t>[0])}
                className="min-h-[60px] text-xs"
                rows={2}
              />
              <Input
                value={editTags}
                onChange={(e) => setEditTags(e.target.value)}
                placeholder={t(
                  "skill.namePlaceholder" as Parameters<typeof t>[0],
                )}
                className="h-7 text-xs"
              />
              <div className="flex gap-1.5">
                <Button
                  size="sm"
                  className="h-6 text-2xs"
                  onClick={handleMetaSave}
                  disabled={metaSaving}
                >
                  <Check className="mr-0.5 h-3 w-3" />
                  {t("common.save" as Parameters<typeof t>[0])}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-2xs"
                  onClick={() => {
                    setMetaEditing(false);
                    setEditName(skill?.name ?? "");
                    setEditDesc(skill?.description ?? "");
                    setEditTags(skill?.tags?.join(", ") ?? "");
                  }}
                >
                  <X className="mr-0.5 h-3 w-3" />
                  {t("common.cancel" as Parameters<typeof t>[0])}
                </Button>
              </div>
            </div>
          ) : (
            <div>
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-light text-brand">
                    <Bot className="h-3.5 w-3.5" />
                  </div>
                  <div className="min-w-0 text-sm font-medium text-ink-heading">
                    {skill?.name || decodedId}
                  </div>
                </div>
                <button
                  type="button"
                  className="mt-0.5 shrink-0 rounded p-1 text-ink-meta transition-colors hover:bg-surface-muted hover:text-ink-heading"
                  onClick={() => setMetaEditing(true)}
                >
                  <FilePenLine className="h-3 w-3" />
                </button>
              </div>
              {skill?.description && (
                <p className="mt-1.5 text-2xs leading-4 text-ink-body">
                  {skill.description}
                </p>
              )}
              <div className="mt-2 flex flex-wrap gap-1">
                {skill?.scope && (
                  <Badge variant="outline" className="text-[10px]">
                    {skill.scope}
                  </Badge>
                )}
                {skill?.tags?.map((tag) => (
                  <Badge key={tag} variant="brand" className="text-[10px]">
                    {tag}
                  </Badge>
                ))}
              </div>
              <div className="mt-2 text-2xs text-ink-meta">
                {files.length}{" "}
                {t("skill.fileStructure" as Parameters<typeof t>[0])}
                {skill?.version ? ` · v${skill.version}` : ""}
              </div>
              {skill?.origin && (
                <a
                  href={skill.origin.source_url}
                  target="_blank"
                  rel="noreferrer"
                  title={skill.origin.source_url}
                  className="mt-2 flex items-center gap-1 text-2xs text-ink-meta transition-colors hover:text-brand"
                >
                  <ExternalLink className="h-3 w-3 shrink-0" />
                  <span className="truncate">
                    {t("skill.importedFrom" as Parameters<typeof t>[0], {
                      source:
                        skill.origin.type === "github" ? "GitHub" : "URL",
                    })}
                    {skill.origin.path ? ` · ${skill.origin.path}` : ""}
                  </span>
                </a>
              )}
            </div>
          )}
        </div>

        {/* File tree */}
        <div className="overflow-y-auto p-2">
          <FileTreeNode
            files={files}
            selectedFile={selectedFile}
            onSelect={setSelectedFile}
            depth={0}
          />
        </div>
      </div>

      {/* Right: file content area */}
      <div className="flex flex-1 flex-col">
        {/* File toolbar */}
        <div className="flex items-center justify-between border-b border-surface-border px-4 py-2">
          <div className="flex items-center gap-2">
            <FileText className="h-3.5 w-3.5 text-ink-muted" />
            <span className="text-xs font-medium text-ink-label">
              {selectedFile || "—"}
            </span>
          </div>
          <div className="flex items-center gap-1">
            {viewMode === "edit" ? (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-2xs"
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : null}
                  {t("common.save" as Parameters<typeof t>[0])}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-2xs"
                  onClick={() => setViewMode("preview")}
                >
                  <Eye className="mr-1 h-3 w-3" />
                  {t("common.preview" as Parameters<typeof t>[0])}
                </Button>
              </>
            ) : (
              <>
                {isMarkdownFile && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-2xs"
                    onClick={() =>
                      setViewMode(viewMode === "preview" ? "source" : "preview")
                    }
                  >
                    {viewMode === "preview"
                      ? t("common.other" as Parameters<typeof t>[0])
                      : t("common.preview" as Parameters<typeof t>[0])}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-2xs"
                  onClick={() => {
                    setEditContent(fileContent);
                    setViewMode("edit");
                  }}
                >
                  <FilePenLine className="mr-1 h-3 w-3" />
                  {t("common.edit" as Parameters<typeof t>[0])}
                </Button>
              </>
            )}
          </div>
        </div>

        {/* File content */}
        <div className="flex-1 overflow-auto p-4">
          {!selectedFile ? (
            <div className="flex h-full items-center justify-center text-sm text-ink-meta">
              {t("skill.selectFileToPreview" as Parameters<typeof t>[0])}
            </div>
          ) : viewMode === "edit" ? (
            <Textarea
              value={editContent}
              onChange={(e) => handleEditChange(e.target.value)}
              className="min-h-full font-mono text-xs"
              rows={20}
            />
          ) : viewMode === "preview" && isMarkdownFile ? (
            <MarkdownContent content={fileContent} />
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-ink-label">
              {fileContent}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
};

function FileTreeNode({
  files,
  selectedFile,
  onSelect,
  depth,
}: {
  files: TreeNode[];
  selectedFile: string | null;
  onSelect: (path: string) => void;
  depth: number;
}) {
  return (
    <>
      {files.map((file) => {
        if (file.type === "directory") {
          return (
            <div key={file.path}>
              <div
                className="flex items-center gap-1.5 rounded px-1 py-0.5 text-xs text-ink-label hover:bg-surface-muted"
                style={{ paddingLeft: `${depth * 14 + 4}px` }}
              >
                <span className="shrink-0 text-[11px]">📁</span>
                <span className="truncate">{file.name}/</span>
              </div>
              {file.children && (
                <FileTreeNode
                  files={file.children}
                  selectedFile={selectedFile}
                  onSelect={onSelect}
                  depth={depth + 1}
                />
              )}
            </div>
          );
        }

        return (
          <button
            key={file.path}
            type="button"
            onClick={() => onSelect(file.path)}
            className={`flex w-full items-center gap-1.5 rounded-[4px] px-1 py-0.5 text-left text-xs transition hover:bg-surface-muted ${selectedFile === file.path ? "bg-surface-soft text-ink-heading" : "text-ink-label"}`}
            style={{ paddingLeft: `${depth * 14 + 22}px` }}
          >
            <span className="shrink-0 text-[11px]">
              {file.name.endsWith(".md")
                ? "📝"
                : file.name.endsWith(".json")
                  ? "{ }"
                  : "📄"}
            </span>
            <span className="truncate">{file.name}</span>
          </button>
        );
      })}
    </>
  );
}
