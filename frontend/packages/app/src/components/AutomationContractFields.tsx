/**
 * Execution-kind switch (Agent / Code) + code fields + the "Advanced:
 * contracts" JSON disclosure — extracted out of ``CreateAutomationDialog``
 * per the file-size rule (frontend/CLAUDE.md). Fully controlled: the parent
 * owns every value, this component only renders the fields and calls back.
 */
import { ChevronRight } from "lucide-react";
import { useI18n } from "@valuz/ui";
import {
  FormField,
  Input,
  SegmentedControl,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
  cn,
} from "@valuz/ui";

export type AutomationExecutionKind = "agent" | "code";
export type AutomationCodeRuntime = "python" | "shell";

export interface AutomationContractFieldsProps {
  executionKind: AutomationExecutionKind;
  onExecutionKindChange: (kind: AutomationExecutionKind) => void;

  codeRuntime: AutomationCodeRuntime;
  onCodeRuntimeChange: (runtime: AutomationCodeRuntime) => void;
  codeEntry: string;
  onCodeEntryChange: (entry: string) => void;
  codeTimeoutSeconds: number;
  onCodeTimeoutSecondsChange: (seconds: number) => void;

  contractsOpen: boolean;
  onContractsOpenChange: (open: boolean) => void;
  inputContractText: string;
  onInputContractTextChange: (text: string) => void;
  resultContractText: string;
  onResultContractTextChange: (text: string) => void;
}

const MIN_CODE_TIMEOUT_SECONDS = 1;
const MAX_CODE_TIMEOUT_SECONDS = 3600;

export const AutomationContractFields = ({
  executionKind,
  onExecutionKindChange,
  codeRuntime,
  onCodeRuntimeChange,
  codeEntry,
  onCodeEntryChange,
  codeTimeoutSeconds,
  onCodeTimeoutSecondsChange,
  contractsOpen,
  onContractsOpenChange,
  inputContractText,
  onInputContractTextChange,
  resultContractText,
  onResultContractTextChange,
}: AutomationContractFieldsProps) => {
  const { t } = useI18n();

  return (
    <>
      <FormField
        label={t("automation.executionKindLabel" as Parameters<typeof t>[0])}
      >
        <SegmentedControl
          value={executionKind}
          onValueChange={(v) =>
            onExecutionKindChange(v as AutomationExecutionKind)
          }
          className="h-8 w-fit"
          options={[
            {
              value: "agent" as const,
              label: t(
                "automation.executionKindAgent" as Parameters<typeof t>[0],
              ),
            },
            {
              value: "code" as const,
              label: t(
                "automation.executionKindCode" as Parameters<typeof t>[0],
              ),
            },
          ]}
        />
      </FormField>

      {executionKind === "code" ? (
        <div className="grid grid-cols-2 items-start gap-2">
          <FormField
            label={t("automation.codeRuntimeLabel" as Parameters<typeof t>[0])}
          >
            <Select
              value={codeRuntime}
              onValueChange={(v) =>
                v && onCodeRuntimeChange(v as AutomationCodeRuntime)
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="python">
                  {t("automation.codeRuntimePython" as Parameters<typeof t>[0])}
                </SelectItem>
                <SelectItem value="shell">
                  {t("automation.codeRuntimeShell" as Parameters<typeof t>[0])}
                </SelectItem>
              </SelectContent>
            </Select>
          </FormField>
          <FormField
            label={t("automation.codeTimeoutLabel" as Parameters<typeof t>[0])}
          >
            <Input
              type="number"
              min={MIN_CODE_TIMEOUT_SECONDS}
              max={MAX_CODE_TIMEOUT_SECONDS}
              value={codeTimeoutSeconds}
              onChange={(e) => {
                const v = Number.parseInt(e.target.value, 10);
                if (Number.isFinite(v)) onCodeTimeoutSecondsChange(v);
              }}
            />
          </FormField>
          <div className="col-span-2 min-w-0">
            <FormField
              label={t("automation.codeEntryLabel" as Parameters<typeof t>[0])}
            >
              <Input
                value={codeEntry}
                onChange={(e) => onCodeEntryChange(e.target.value)}
                placeholder={t(
                  "automation.codeEntryPlaceholder" as Parameters<typeof t>[0],
                )}
              />
            </FormField>
          </div>
        </div>
      ) : null}

      <div className="border-t border-surface-border pt-3">
        <button
          type="button"
          aria-expanded={contractsOpen}
          onClick={() => onContractsOpenChange(!contractsOpen)}
          className="flex items-center gap-1 text-xs font-medium text-ink-heading"
        >
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 text-ink-meta transition-transform",
              contractsOpen && "rotate-90",
            )}
          />
          {t("automation.contractsAdvanced" as Parameters<typeof t>[0])}
        </button>
        {contractsOpen ? (
          <div className="mt-3 flex flex-col gap-[14px]">
            <FormField
              label={t(
                "automation.inputContractLabel" as Parameters<typeof t>[0],
              )}
            >
              <Textarea
                value={inputContractText}
                onChange={(e) => onInputContractTextChange(e.target.value)}
                rows={3}
                className="field-sizing-fixed h-20 resize-none font-mono text-xs"
              />
            </FormField>
            <FormField
              label={t(
                "automation.resultContractLabel" as Parameters<typeof t>[0],
              )}
            >
              <Textarea
                value={resultContractText}
                onChange={(e) => onResultContractTextChange(e.target.value)}
                rows={3}
                className="field-sizing-fixed h-20 resize-none font-mono text-xs"
              />
            </FormField>
          </div>
        ) : null}
      </div>
    </>
  );
};
