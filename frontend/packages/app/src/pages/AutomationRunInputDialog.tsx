import { useEffect, useState } from "react";
import { useTranslation, type InputContract } from "@valuz/core";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Textarea,
} from "@valuz/ui";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

/** Property names + required markers from a JSON-input contract's schema —
 *  a lightweight hint, not full schema validation (the server validates for
 *  real). Anything that isn't a plain object schema renders no hint. */
function schemaHint(schema: Record<string, unknown>): string | null {
  const properties = schema.properties;
  if (!properties || typeof properties !== "object") return null;
  const required = new Set(
    Array.isArray(schema.required) ? (schema.required as unknown[]) : [],
  );
  const names = Object.keys(properties as Record<string, unknown>);
  if (names.length === 0) return null;
  return names
    .map((name) => (required.has(name) ? `${name}*` : name))
    .join(", ");
}

export interface AutomationRunInputDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The automation's input contract — only ``text`` / ``json`` open this
   *  dialog; ``none`` runs immediately without it. */
  inputContract: InputContract;
  busy?: boolean;
  onSubmit: (input: string | Record<string, unknown>) => void | Promise<void>;
}

/**
 * "Run now" input prompt for an automation whose input contract is
 * ``text`` or ``json``. A single textarea for text; a JSON textarea
 * (prefilled with the contract's default) plus a property-name hint for
 * json. Validates ``JSON.parse`` client-side before submitting — the
 * server still validates against the schema.
 */
export const AutomationRunInputDialog = ({
  open,
  onOpenChange,
  inputContract,
  busy = false,
  onSubmit,
}: AutomationRunInputDialogProps) => {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [jsonText, setJsonText] = useState("{}");
  const [jsonError, setJsonError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (inputContract.kind === "text") {
      setText(inputContract.default ?? "");
    } else if (inputContract.kind === "json") {
      setJsonText(JSON.stringify(inputContract.default ?? {}, null, 2));
    }
    setJsonError(null);
  }, [open, inputContract]);

  const isJson = inputContract.kind === "json";
  const hint = isJson ? schemaHint(inputContract.schema) : null;

  const handleSubmit = async () => {
    if (isJson) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(jsonText);
      } catch {
        setJsonError(t(k("automation.runInputInvalidJson")));
        return;
      }
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        Array.isArray(parsed)
      ) {
        setJsonError(t(k("automation.runInputInvalidJson")));
        return;
      }
      setJsonError(null);
      await onSubmit(parsed as Record<string, unknown>);
      return;
    }
    await onSubmit(text);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t(k("automation.runInputDialogTitle"))}</DialogTitle>
          <DialogDescription>
            {t(k("automation.runInputDialogDesc"))}
          </DialogDescription>
        </DialogHeader>
        {isJson ? (
          <FormField label={t(k("automation.runInputJsonLabel"))}>
            <Textarea
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              rows={8}
              className="field-sizing-fixed h-48 resize-none font-mono text-xs"
            />
            {hint ? (
              <p className="mt-1 text-2xs leading-4 text-ink-meta">
                {t(k("automation.runInputJsonHint"), { fields: hint })}
              </p>
            ) : null}
            {jsonError ? (
              <p className="mt-1 text-2xs leading-4 text-error-text">
                {jsonError}
              </p>
            ) : null}
          </FormField>
        ) : (
          <FormField label={t(k("automation.runInputTextLabel"))}>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              className="field-sizing-fixed h-28 resize-none"
            />
          </FormField>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            {t(k("common.cancel"))}
          </Button>
          <Button onClick={() => void handleSubmit()} loading={busy}>
            {t(k("cron.runNow"))}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
