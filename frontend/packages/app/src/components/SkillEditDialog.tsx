import { useEffect, useState } from "react";
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
  Textarea,
} from "@valuz/ui";
import { useTranslation, type SkillView } from "@valuz/core";
import { toast } from "sonner";

interface SkillEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  skill: SkillView | null;
  onSubmit: (
    skillId: string,
    data: { name?: string; description?: string },
  ) => Promise<void>;
  onComplete: () => void;
}

export function SkillEditDialog({
  open,
  onOpenChange,
  skill,
  onSubmit,
  onComplete,
}: SkillEditDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open && skill) {
      setName(skill.name);
      setDesc(skill.description);
    }
  }, [open, skill]);

  const handleClose = () => {
    onOpenChange(false);
  };

  const handleSave = async () => {
    if (!skill) return;
    setSubmitting(true);
    try {
      await onSubmit(skill.id, {
        name: name || undefined,
        description: desc || undefined,
      });
      handleClose();
      onComplete();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.saveFailed" as Parameters<typeof t>[0]),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) handleClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("skill.editSkill" as Parameters<typeof t>[0])}
          </DialogTitle>
          <DialogDescription>
            {t("skill.editSkillDesc" as Parameters<typeof t>[0])}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <FormField label={t("common.name" as Parameters<typeof t>[0])}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t(
                "skill.namePlaceholder" as Parameters<typeof t>[0],
              )}
            />
          </FormField>
          <FormField label={t("common.description" as Parameters<typeof t>[0])}>
            <Textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder={t(
                "skill.descPlaceholder" as Parameters<typeof t>[0],
              )}
              rows={3}
            />
          </FormField>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
          <Button onClick={handleSave} loading={submitting}>
            {t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
