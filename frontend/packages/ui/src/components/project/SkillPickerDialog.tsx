import { useState } from "react";
import { Bot, Search } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../ui/dialog";
import { Checkbox } from "../ui/checkbox";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface SkillPickerItem {
  id: string;
  name: string;
  description?: string;
  scope: "user" | "project";
  path: string;
  enabled: boolean;
}

export interface SkillPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  skills: SkillPickerItem[];
  onToggle: (path: string, enabled: boolean) => void;
}

export const SkillPickerDialog = ({
  open,
  onOpenChange,
  skills,
  onToggle,
}: SkillPickerDialogProps) => {
  const { t } = useI18n();
  const [search, setSearch] = useState("");

  const filtered = search
    ? skills.filter(
        (s) =>
          s.name.toLowerCase().includes(search.toLowerCase()) ||
          (s.description?.toLowerCase().includes(search.toLowerCase()) ??
            false),
      )
    : skills;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("skill.addSkill")}</DialogTitle>
          <DialogDescription>{t("skill.addSkillDesc")}</DialogDescription>
        </DialogHeader>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("skill.searchPlaceholder")}
            className="h-8 w-full rounded-md border border-surface-border bg-card pl-8 pr-3 text-sm text-ink-label placeholder:text-ink-muted focus:border-brand focus:outline-none"
          />
        </div>

        <div className="max-h-[320px] space-y-1.5 overflow-y-auto">
          {filtered.length > 0 ? (
            filtered.map((skill) => (
              <label
                key={skill.id}
                className="flex items-start gap-2.5 rounded-lg border border-surface-border bg-card px-3 py-2.5 transition hover:border-brand/30"
              >
                <Checkbox
                  checked={skill.enabled}
                  onCheckedChange={(checked) =>
                    onToggle(skill.path, checked === true)
                  }
                  className="mt-0.5"
                />
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-brand-light text-brand">
                  <Bot className="h-3 w-3" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-xs font-medium text-ink-heading">
                      {skill.name}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 rounded px-1 py-px text-2xs font-medium",
                        skill.scope === "project"
                          ? "bg-[#f3f2ff] text-[#725cf9]"
                          : "bg-surface-soft text-ink-meta",
                      )}
                    >
                      {skill.scope === "project"
                        ? t("skill.project")
                        : t("skill.user")}
                    </span>
                  </div>
                  {skill.description && (
                    <p className="mt-0.5 line-clamp-2 text-2xs text-ink-body">
                      {skill.description}
                    </p>
                  )}
                </div>
              </label>
            ))
          ) : (
            <div className="py-6 text-center text-xs text-ink-meta">
              {search ? t("skill.noMatch") : t("skill.noAvailable")}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
