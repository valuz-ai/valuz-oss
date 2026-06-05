import { useState, type ReactNode } from "react";
import {
  ChevronRight,
  Database,
  FilePenLine,
  FileText,
  Plus,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../hooks/use-i18n";

export interface ContextPanelSkillItem {
  id?: string;
  name: string;
  on: boolean;
  disabled?: boolean;
}

export interface ContextPanelDocumentItem {
  id?: string;
  name: string;
  meta: string;
  kind?: "uploaded" | "kb";
  onRemove?: () => void;
}

export interface ContextPanelProps {
  instructions?: string;
  memories?: string[];
  skills?: ContextPanelSkillItem[];
  documents?: ContextPanelDocumentItem[];
  onEditInstructions?: () => void;
  onAddMemory?: () => void;
  onToggleSkill?: (skillId: string) => void;
  onAddSkill?: () => void;
  onAttachDocuments?: () => void;
  onUploadFile?: () => void;
  onRemoveDocument?: (id: string) => void;
}

const FALLBACK_SKILLS: ContextPanelSkillItem[] = [
  { name: "研报撰写模板", on: true },
  { name: "DCF 估值", on: true },
  { name: "行业对比框架", on: false },
];

const SectionTitle = ({
  title,
  action,
}: {
  title: string;
  action?: ReactNode;
}) => (
  <div className="mb-3 flex items-center gap-2">
    <div className="text-sm font-medium text-ink-heading">{title}</div>
    <div className="flex-1" />
    {action}
  </div>
);

const CollapsibleSection = ({
  icon,
  title,
  count,
  defaultOpen = true,
  children,
}: {
  icon: ReactNode;
  title: string;
  count?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="overflow-hidden rounded-[10px] border border-surface-border bg-surface-soft">
      <button
        type="button"
        className="flex h-10 w-full items-center gap-2 px-3 transition-colors hover:bg-surface-muted/60"
        onClick={() => setOpen((current) => !current)}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-ink-body transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center text-ink-body">
          {icon}
        </span>
        <span className="text-xs font-medium tracking-[0.8px] text-ink-label">
          {title}
        </span>
        {count ? (
          <span className="ml-auto text-xs text-ink-meta">{count}</span>
        ) : null}
      </button>
      {open ? <div className="px-4 pb-3.5">{children}</div> : null}
    </div>
  );
};

export const ChatContextPanel = ({
  instructions,
  skills: skillItems,
  documents: documentItems,
  onEditInstructions,
  onToggleSkill,
  onAddSkill,
  onAttachDocuments,
  onUploadFile,
  onRemoveDocument,
}: ContextPanelProps = {}) => {
  const { t } = useI18n();
  const docs = documentItems ?? [];
  const uploadedDocs = docs.filter((d) => d.kind === "uploaded");
  const kbDocs = docs.filter((d) => d.kind === "kb");
  const hasAnyDocs = docs.length > 0;

  return (
    <div className="flex h-full flex-col" style={{ width: 337 }}>
      <div className="flex h-14 shrink-0 items-center border-b border-surface-border px-5">
        <span className="text-base font-medium text-ink-heading">Context</span>
      </div>

      <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
        {/* Instructions */}
        {instructions ? (
          <div className="rounded-[10px] border border-surface-border bg-surface-soft p-4">
            <SectionTitle
              title="Instructions"
              action={
                onEditInstructions ? (
                  <button
                    type="button"
                    className="flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-surface"
                    onClick={onEditInstructions}
                  >
                    <FilePenLine className="h-3.5 w-3.5 text-ink-meta" />
                  </button>
                ) : undefined
              }
            />
            <p className="text-sm italic leading-6 text-ink-body line-clamp-6">
              {instructions}
            </p>
          </div>
        ) : null}

        {/* Skills */}
        {(skillItems ?? FALLBACK_SKILLS).length > 0 ? (
          <div className="rounded-[10px] border border-surface-border bg-surface-soft p-4">
            <SectionTitle
              title="Skills"
              action={
                onAddSkill ? (
                  <button
                    type="button"
                    className="flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-surface"
                    onClick={onAddSkill}
                  >
                    <Plus className="h-3.5 w-3.5 text-ink-meta" />
                  </button>
                ) : undefined
              }
            />
            <div className="space-y-2">
              {(skillItems ?? FALLBACK_SKILLS).map((skill) => (
                <div
                  key={skill.name}
                  className="flex items-center gap-3 rounded-lg px-2 py-1.5"
                >
                  <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-surface-border bg-surface text-brand">
                    <Sparkles className="h-3.5 w-3.5" />
                  </div>
                  <span
                    className={cn(
                      "flex-1 text-sm",
                      skill.on ? "text-ink-heading" : "text-ink-body",
                    )}
                  >
                    {skill.name}
                  </span>
                  <button
                    type="button"
                    disabled={!skill.id || skill.disabled || !onToggleSkill}
                    className={cn(
                      "relative h-4 w-7 rounded-full transition-colors",
                      skill.on ? "bg-brand" : "bg-surface-border-hover",
                      (!skill.id || skill.disabled || !onToggleSkill) &&
                        "cursor-default opacity-70",
                    )}
                    onClick={() => {
                      if (skill.id && onToggleSkill) {
                        onToggleSkill(skill.id);
                      }
                    }}
                  >
                    <span
                      className={cn(
                        "absolute top-[2px] h-3 w-3 rounded-full bg-white transition-all",
                        skill.on ? "left-4" : "left-[2px]",
                      )}
                    />
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Documents — uploaded + KB */}
        <CollapsibleSection
          icon={<FileText className="h-3.5 w-3.5" />}
          title="Documents"
          count={hasAnyDocs ? String(docs.length) : undefined}
        >
          <div className="space-y-2">
            {uploadedDocs.length > 0 && (
              <div className="text-[10px] uppercase tracking-[0.7px] text-ink-section">
                {t("knowledge.uploaded")}
              </div>
            )}
            {uploadedDocs.map((doc) => (
              <div
                key={doc.name}
                className="group flex items-center gap-2 rounded-[8px] border border-surface-border bg-surface px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-ink-heading">
                    {doc.name}
                  </div>
                  {doc.meta ? (
                    <div className="mt-0.5 font-mono text-2xs text-ink-meta">
                      {doc.meta}
                    </div>
                  ) : null}
                </div>
                {doc.id && onRemoveDocument ? (
                  <button
                    type="button"
                    className="shrink-0 rounded p-0.5 text-ink-meta opacity-0 transition-opacity hover:bg-surface-soft group-hover:opacity-100"
                    onClick={doc.onRemove ?? (() => onRemoveDocument(doc.id!))}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </div>
            ))}
            {kbDocs.length > 0 && (
              <>
                {uploadedDocs.length > 0 && (
                  <div className="my-1 border-t border-surface-border" />
                )}
                <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.7px] text-ink-section">
                  <Database className="h-3 w-3" />{" "}
                  {t("knowledge.knowledgeBase")}
                </div>
              </>
            )}
            {kbDocs.map((doc) => (
              <div
                key={doc.id ?? doc.name}
                className="rounded-[8px] border border-surface-border bg-surface px-3 py-2.5"
              >
                <div className="truncate text-sm text-ink-heading">
                  {doc.name}
                </div>
                <div className="mt-0.5 font-mono text-2xs text-ink-meta">
                  {doc.meta}
                </div>
              </div>
            ))}
            {!hasAnyDocs && (
              <div className="py-2 text-center text-xs text-ink-meta">
                {t("knowledge.noRefDocs")}
              </div>
            )}
            {onAttachDocuments && (
              <button
                type="button"
                className="w-full rounded-[8px] border border-dashed border-surface-border-hover py-2 text-xs text-ink-body transition-colors hover:bg-surface"
                onClick={onAttachDocuments}
              >
                {t("knowledge.refFromKb")}
              </button>
            )}
          </div>
        </CollapsibleSection>

        {/* Upload file */}
        {onUploadFile && (
          <button
            type="button"
            className="flex items-center gap-3 rounded-[10px] border border-surface-border bg-surface-soft px-4 py-3 text-left transition-colors hover:bg-surface-muted/60"
            onClick={onUploadFile}
          >
            <Upload className="h-4 w-4 text-ink-body" />
            <span className="flex-1 text-sm text-ink-heading">
              {t("knowledge.uploadFiles")}
            </span>
          </button>
        )}
      </div>
    </div>
  );
};

export const ProjectContextPanel = (props: ContextPanelProps) => (
  <ChatContextPanel {...props} />
);
