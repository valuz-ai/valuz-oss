import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@valuz/ui/components/ui/command";
import {
  MessageSquarePlus,
  FolderPlus,
  Settings,
  Sparkles,
  Upload,
  BookOpen,
  Clock,
} from "lucide-react";
import type { ReactNode } from "react";
import { useI18n } from "../hooks/use-i18n";

interface PaletteItem {
  icon: ReactNode;
  label: string;
  shortcut?: string;
}

const items: Record<string, PaletteItem[]> = {
  最近: [
    { icon: <MessageSquarePlus className="h-4 w-4" />, label: "开始新对话" },
    { icon: <FolderPlus className="h-4 w-4" />, label: "切换到英伟达研究项目" },
  ],
  命令: [
    {
      icon: <MessageSquarePlus className="h-4 w-4" />,
      label: "新建对话",
      shortcut: "⌘N",
    },
    {
      icon: <FolderPlus className="h-4 w-4" />,
      label: "新建项目",
      shortcut: "⌘⇧N",
    },
    {
      icon: <Settings className="h-4 w-4" />,
      label: "打开设置",
      shortcut: "⌘,",
    },
    {
      icon: <Upload className="h-4 w-4" />,
      label: "上传文件到知识库",
      shortcut: "⌘U",
    },
  ],
  技能: [
    { icon: <Sparkles className="h-4 w-4" />, label: "研报撰写模板" },
    { icon: <Sparkles className="h-4 w-4" />, label: "行业对比框架" },
    { icon: <Sparkles className="h-4 w-4" />, label: "财报要点提取" },
  ],
  工作空间: [
    { icon: <BookOpen className="h-4 w-4" />, label: "英伟达 2025 深度研究" },
    { icon: <BookOpen className="h-4 w-4" />, label: "新能源行业周报" },
    { icon: <Clock className="h-4 w-4" />, label: "消费电子竞争格局" },
  ],
};

export const CommandPalette = ({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) => {
  const { t } = useI18n();
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
        onClick={() => onOpenChange(false)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onOpenChange(false);
        }}
        role="button"
        tabIndex={-1}
        aria-label={t("commandPalette.close")}
      />
      <div className="absolute left-1/2 top-[20%] z-50 w-full max-w-[640px] -translate-x-1/2 overflow-hidden rounded-2xl border border-surface-border bg-surface shadow-lg">
        <Command>
          <div className="bg-surface-soft/50">
            <CommandInput
              placeholder={t("commandPalette.placeholder")}
              className="h-12 bg-transparent text-sm text-ink-heading placeholder:text-ink-muted focus:outline-none"
            />
          </div>
          <CommandList className="max-h-[380px] p-2">
            <CommandEmpty className="py-8 text-center text-sm text-ink-muted">
              {t("commandPalette.noMatch")}
            </CommandEmpty>
            {Object.entries(items).map(([group, list]) => (
              <CommandGroup
                key={group}
                heading={
                  <div className="label-mono px-2 pb-1 pt-2">{group}</div>
                }
              >
                {list.map((it) => (
                  <CommandItem
                    key={it.label}
                    className="flex items-center gap-3 rounded-md px-3 py-2.5 text-sm text-ink-label data-[selected=true]:bg-brand-light data-[selected=true]:text-brand transition-colors"
                  >
                    <span className="flex h-4 w-4 items-center justify-center text-ink-body">
                      {it.icon}
                    </span>
                    <span className="flex-1">{it.label}</span>
                    {it.shortcut && (
                      <kbd className="rounded-sm bg-surface-muted px-1.5 py-0.5 font-mono text-2xs text-ink-muted tabular">
                        {it.shortcut}
                      </kbd>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
          </CommandList>
          <div className="flex items-center gap-4 bg-surface-soft/50 px-4 py-3 text-2xs text-ink-muted">
            <span className="flex items-center gap-1">
              <kbd className="rounded-sm bg-surface-muted px-1 py-0.5 font-mono tabular">
                ↑↓
              </kbd>
              {t("commandPalette.navigate")}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded-sm bg-surface-muted px-1 py-0.5 font-mono tabular">
                ⏎
              </kbd>
              {t("commandPalette.select")}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded-sm bg-surface-muted px-1 py-0.5 font-mono tabular">
                Esc
              </kbd>
              {t("commandPalette.close")}
            </span>
          </div>
        </Command>
      </div>
    </div>
  );
};
