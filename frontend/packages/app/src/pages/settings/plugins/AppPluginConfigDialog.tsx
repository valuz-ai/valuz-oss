import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  FormField,
  Input,
  LoadingState,
  NativeSelect,
  NativeSelectOption,
  Switch,
  Textarea,
} from "@valuz/ui";
import { appPluginsApi, useTranslation } from "@valuz/core";
import { getLocale } from "@valuz/shared/i18n";
import { Notice } from "./Notice";
import {
  buildConfigPayload,
  errorDetail,
  initialConfigDraft,
  parseConfigSchema,
  serverErrorList,
  validateConfigDraft,
  type ConfigDraft,
  type ConfigErrorCode,
  type ConfigField,
} from "./app-plugin-helpers";

interface Loaded {
  values: Record<string, unknown>;
  fields: ConfigField[];
}

type LoadState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; loaded: Loaded };

const fieldId = (key: string) => `app-plugin-config-${key}`;

const FieldControl = ({
  field,
  value,
  invalid,
  disabled,
  onChange,
}: {
  field: ConfigField;
  value: string | boolean;
  invalid: boolean;
  disabled: boolean;
  onChange: (next: string | boolean) => void;
}) => {
  const { t } = useTranslation();
  const id = fieldId(field.key);
  switch (field.kind) {
    case "boolean":
      return (
        <Switch
          id={id}
          size="sm"
          checked={value === true}
          disabled={disabled}
          onCheckedChange={onChange}
        />
      );
    case "enum":
      return (
        <NativeSelect
          id={id}
          value={String(value)}
          disabled={disabled}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        >
          {/* An optional enum can stay unset; a required one shows the
              blank only until a member is picked. */}
          {!field.required || value === "" ? (
            <NativeSelectOption value="">
              {t("pluginSettings.appPlugins.config.unset")}
            </NativeSelectOption>
          ) : null}
          {field.options.map((option) => (
            <NativeSelectOption key={String(option)} value={String(option)}>
              {String(option)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      );
    case "string-list":
      return (
        <Textarea
          id={id}
          value={String(value)}
          rows={3}
          disabled={disabled}
          aria-invalid={invalid}
          placeholder={t("pluginSettings.appPlugins.config.listHint")}
          onChange={(event) => onChange(event.target.value)}
        />
      );
    case "number":
    case "integer":
      return (
        <Input
          id={id}
          value={String(value)}
          inputMode={field.kind === "integer" ? "numeric" : "decimal"}
          disabled={disabled}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      );
    default:
      return (
        <Input
          id={id}
          value={String(value)}
          disabled={disabled}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      );
  }
};

/**
 * The plugin's settings form, generated from the manifest's ``config`` JSON
 * Schema (string / number / integer / boolean / enum / array of strings).
 * Required fields are checked here; whatever the server still rejects comes
 * back as text and is shown in place.
 */
export const AppPluginConfigDialog = ({
  pluginId,
  name,
  schema,
  onClose,
}: {
  pluginId: string;
  name: string;
  /** The schema on the list item; the config response's own wins when present. */
  schema: Record<string, unknown> | null;
  onClose: () => void;
}) => {
  const { t } = useTranslation();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [draft, setDraft] = useState<ConfigDraft>({});
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<{
    message: string;
    errors: string[];
  } | null>(null);

  useEffect(() => {
    let alive = true;
    void appPluginsApi
      .getConfig(pluginId)
      .then(({ values, schema: serverSchema }) => {
        if (!alive) return;
        const fields = parseConfigSchema(serverSchema ?? schema, getLocale());
        setDraft(initialConfigDraft(fields, values));
        setState({ phase: "ready", loaded: { values, fields } });
      })
      .catch((error: unknown) => {
        if (alive) setState({ phase: "error", message: errorDetail(error) });
      });
    return () => {
      alive = false;
    };
    // ``schema`` is only the fallback for the first read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pluginId]);

  const fields = useMemo<ConfigField[]>(
    () => (state.phase === "ready" ? state.loaded.fields : []),
    [state],
  );
  const errors = useMemo<Record<string, ConfigErrorCode>>(
    () => validateConfigDraft(fields, draft),
    [fields, draft],
  );

  const errorText = (field: ConfigField): string | undefined => {
    const code = errors[field.key];
    if (!submitted || !code) return undefined;
    const key =
      code === "required"
        ? "pluginSettings.appPlugins.config.required"
        : code === "invalidInteger"
          ? "pluginSettings.appPlugins.config.invalidInteger"
          : "pluginSettings.appPlugins.config.invalidNumber";
    return t(key, { field: field.label });
  };

  const save = async () => {
    if (state.phase !== "ready") return;
    setSubmitted(true);
    if (Object.keys(errors).length > 0) return;
    setSaving(true);
    setFailure(null);
    try {
      await appPluginsApi.putConfig(
        pluginId,
        buildConfigPayload(fields, draft, state.loaded.values),
      );
      toast.success(t("pluginSettings.appPlugins.config.saved"));
      onClose();
    } catch (error) {
      setFailure({
        message: t("pluginSettings.appPlugins.config.saveFailed", {
          error: errorDetail(error),
        }),
        errors: serverErrorList(error),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader className="pr-8">
          <DialogTitle>
            {t("pluginSettings.appPlugins.config.title", { name })}
          </DialogTitle>
          <DialogDescription>
            {t("pluginSettings.appPlugins.config.desc")}
          </DialogDescription>
        </DialogHeader>

        {state.phase === "loading" ? (
          <LoadingState variant="section" />
        ) : state.phase === "error" ? (
          <Notice tone="error">
            {t("pluginSettings.appPlugins.config.loadFailed", {
              error: state.message,
            })}
          </Notice>
        ) : fields.length === 0 ? (
          <EmptyState message={t("pluginSettings.appPlugins.config.noSchema")} />
        ) : (
          <form
            className="space-y-4"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            {fields.map((field) => (
              <FormField
                key={field.key}
                label={field.required ? `${field.label} *` : field.label}
                htmlFor={fieldId(field.key)}
                error={errorText(field)}
              >
                <FieldControl
                  field={field}
                  value={draft[field.key] ?? ""}
                  invalid={errorText(field) !== undefined}
                  disabled={saving}
                  onChange={(next) =>
                    setDraft((prev) => ({ ...prev, [field.key]: next }))
                  }
                />
                {field.description ? (
                  <p className="mt-1 text-2xs text-ink-meta">
                    {field.description}
                  </p>
                ) : null}
              </FormField>
            ))}
            {failure ? (
              <Notice tone="error">
                <p className="break-words">{failure.message}</p>
                {failure.errors.length > 0 ? (
                  <>
                    <p>{t("pluginSettings.appPlugins.config.serverErrors")}</p>
                    <ul className="list-disc space-y-0.5 pl-4">
                      {failure.errors.map((error) => (
                        <li key={error} className="break-words">
                          {error}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </Notice>
            ) : null}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={saving}
                onClick={onClose}
              >
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={saving} loading={saving}>
                {t("pluginSettings.appPlugins.config.save")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
};
