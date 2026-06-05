import { useState, type FC } from "react";
import { useI18n } from "../../hooks/use-i18n";
import { Check, Eye, EyeOff, Loader2, X } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { cn } from "@valuz/ui/lib/utils";

export type TestStatus = "idle" | "testing" | "ok" | "fail";

export interface ProviderFormFieldsProps {
  /** Current protocol value. */
  protocol: string;
  onProtocolChange: (p: string) => void;
  /** Show the 4-button protocol picker. */
  showProtocol: boolean;
  docsUrl?: string;

  /** API Key value. */
  apiKey: string;
  onApiKeyChange: (v: string) => void;
  /** Hide the field entirely (e.g. OAuth providers). */
  showApiKey?: boolean;
  apiKeyPlaceholder?: string;

  /** Base URL / Endpoint value. */
  baseUrl: string;
  onBaseUrlChange: (v: string) => void;
  /** Show the endpoint input. */
  showEndpoint: boolean;
  endpointReadOnly?: boolean;

  /** Test result display. */
  testStatus: TestStatus;
  testMsg: string;
  /** Known model ids — surfaces as a read-only "可用模型" dropdown.
   *  The list is shown whenever it's non-empty (no testStatus gate), so
   *  callers can seed it from the channel's persisted ``model_options``
   *  on dialog open and let the user browse without re-probing. The
   *  Select is controlled with an empty value — clicks don't persist;
   *  the channel's default model lives in the global Default-config
   *  card. */
  discoveredModels?: string[] | null;

  /** Editable models textarea — only the ``compatible`` (custom) channel
   *  needs this. Anthropic-shape endpoints and many private proxies
   *  don't expose ``/v1/models``, so the user writes the list themselves.
   *  Accepts comma- and newline-separated entries; the parent component
   *  is responsible for parsing on submit. */
  showModelsTextarea?: boolean;
  modelsText?: string;
  onModelsTextChange?: (v: string) => void;
}

const PROTOCOL_OPTIONS = [
  { value: "openai-completion", label: "OpenAI (Chat)" },
  { value: "openai-response", label: "OpenAI (Response)" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
] as const;

export const ProviderFormFields: FC<ProviderFormFieldsProps> = ({
  protocol,
  onProtocolChange,
  showProtocol,
  docsUrl,

  apiKey,
  onApiKeyChange,
  showApiKey = true,
  apiKeyPlaceholder = "sk-...",

  baseUrl,
  onBaseUrlChange,
  showEndpoint,
  endpointReadOnly,

  testStatus,
  testMsg,
  discoveredModels,
  showModelsTextarea = false,
  modelsText = "",
  onModelsTextChange,
}) => {
  const { t } = useI18n();
  const [showKey, setShowKey] = useState(false);

  return (
    <>
      {/* Protocol */}
      {showProtocol && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-ink-label">
            Protocol
          </label>
          <div className="grid grid-cols-2 gap-2">
            {PROTOCOL_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => onProtocolChange(opt.value)}
                className={cn(
                  "rounded-lg border px-3 py-2 text-xs font-medium transition-colors",
                  protocol === opt.value
                    ? "border-brand/40 bg-brand/5 text-brand"
                    : "border-surface-border bg-surface text-ink-body hover:bg-surface-soft",
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {docsUrl && (
            <a
              href={docsUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-block text-2xs text-brand hover:underline"
            >
              {t("ui.providerForm.viewDocs")}
            </a>
          )}
        </div>
      )}

      {/* API Key */}
      {showApiKey && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-ink-label">
            API Key
          </label>
          <div className="relative">
            <input
              type={showKey ? "text" : "password"}
              value={apiKey}
              onChange={(e) => onApiKeyChange(e.target.value.trim())}
              placeholder={apiKeyPlaceholder}
              className={cn(
                "w-full rounded-lg border bg-surface px-3 py-2 pr-10 text-sm text-ink-heading focus:outline-none",
                testStatus === "fail"
                  ? "border-red-400 focus:border-red-400"
                  : "border-surface-border focus:border-brand/30",
              )}
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-meta hover:text-ink-heading"
            >
              {showKey ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
      )}

      {/* Endpoint */}
      {showEndpoint && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-ink-label">
            Endpoint
          </label>
          <input
            type="url"
            value={baseUrl}
            onChange={(e) => onBaseUrlChange(e.target.value)}
            placeholder="https://api.example.com/v1"
            readOnly={endpointReadOnly}
            className={cn(
              "w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm focus:border-brand/30 focus:outline-none",
              endpointReadOnly && "text-ink-meta",
            )}
          />
        </div>
      )}

      {/* Models textarea — compatible (custom) channel only.
          Comma- or newline-separated. Required; parent enforces. */}
      {showModelsTextarea && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-ink-label">
            {t("settings.model.availableModelsList" as Parameters<typeof t>[0])}
            <span className="ml-1 font-normal text-ink-meta">
              {t(
                "settings.model.availableModelsHelp" as Parameters<typeof t>[0],
              )}
            </span>
          </label>
          <textarea
            value={modelsText}
            onChange={(e) => onModelsTextChange?.(e.target.value)}
            placeholder={"model-id-1\nmodel-id-2"}
            rows={3}
            className={cn(
              "w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm focus:border-brand/30 focus:outline-none",
              "font-mono",
            )}
          />
        </div>
      )}

      {/* Test result */}
      {testStatus !== "idle" && (
        <div
          className={cn(
            "flex items-center gap-2 rounded-lg px-3 py-2 text-xs",
            testStatus === "testing" && "bg-surface-soft text-ink-meta",
            testStatus === "ok" && "bg-success/5 text-success",
            testStatus === "fail" && "bg-error-light text-error-text",
          )}
        >
          {testStatus === "testing" && (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          )}
          {testStatus === "ok" && <Check className="h-3.5 w-3.5" />}
          {testStatus === "fail" && <X className="h-3.5 w-3.5" />}
          <span>
            {testStatus === "testing" ? t("ui.providerForm.testing") : testMsg}
          </span>
        </div>
      )}

      {/* Discovered models — read-only dropdown. Renders whenever the
          channel has a known model list (either seeded from the row's
          persisted ``model_options`` on dialog open, or freshly fetched
          by [连接测试]). The Select is controlled with an empty value
          so user clicks don't persist anything — pure browse affordance,
          the channel's default model lives in the Default-config card. */}
      {discoveredModels && discoveredModels.length > 0 && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-ink-label">
            {t("settings.model.availableModels" as Parameters<typeof t>[0], {
              count: String(discoveredModels.length),
            })}
          </label>
          <Select value="" onValueChange={() => {}}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder={discoveredModels[0]} />
            </SelectTrigger>
            <SelectContent>
              {discoveredModels.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </>
  );
};
