import type { ComponentProps } from "react";
import { Renderer } from "@openuidev/react-lang";
import { ThemeProvider } from "@openuidev/react-ui";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import { useI18n } from "../../hooks/use-i18n";
import { Spinner } from "../ui/spinner";

type OpenUiTheme = NonNullable<
  ComponentProps<typeof ThemeProvider>["lightTheme"]
>;

const chartPalette = [
  "var(--accent-sky)",
  "var(--accent-teal)",
  "var(--accent-amber)",
  "var(--accent-pink)",
  "var(--accent-blue)",
  "var(--accent-lime)",
  "var(--accent-orange)",
  "var(--accent-fuchsia)",
];

/** Maps OpenUI directly onto the authoritative Valuz design tokens. */
const VALUZ_OPENUUI_THEME: OpenUiTheme = {
  background: "var(--color-background)",
  foreground: "var(--color-surface)",
  popoverBackground: "var(--color-surface)",
  sunkLight: "var(--color-surface-soft)",
  sunk: "var(--color-surface)",
  sunkDeep: "var(--color-surface-muted)",
  elevatedLight: "var(--color-surface-soft)",
  elevated: "var(--color-surface)",
  elevatedStrong: "var(--color-surface)",
  elevatedIntense: "var(--color-surface)",
  highlightSubtle: "var(--color-surface-soft)",
  highlight: "var(--color-surface-2)",
  highlightStrong: "var(--color-surface-muted)",
  highlightIntense: "var(--color-surface-border)",
  infoBackground: "var(--info-soft)",
  successBackground: "var(--success-soft)",
  alertBackground: "var(--warning-soft)",
  dangerBackground: "var(--error-soft)",

  textNeutralPrimary: "var(--color-ink-heading)",
  textNeutralSecondary: "var(--color-ink-body)",
  textNeutralTertiary: "var(--color-ink-disabled)",
  textNeutralLink: "var(--color-brand)",
  textBrand: "var(--color-brand)",
  textAccentPrimary: "white",
  textAccentSecondary: "var(--color-brand-700)",
  textAccentTertiary: "var(--color-brand)",
  textSuccessPrimary: "var(--success-text)",
  textSuccessInverted: "white",
  textAlertPrimary: "var(--warning-text)",
  textAlertInverted: "var(--foreground)",
  textDangerPrimary: "var(--error-text)",
  textDangerSecondary: "var(--error-text)",
  textDangerTertiary: "var(--color-ink-disabled)",
  textDangerInvertedPrimary: "white",
  textInfoPrimary: "var(--info-text)",
  textInfoInverted: "white",

  interactiveAccentDefault: "var(--color-brand)",
  interactiveAccentHover: "var(--color-brand-hover)",
  interactiveAccentPressed: "var(--color-brand-700)",
  interactiveAccentDisabled:
    "color-mix(in oklab, var(--color-brand) 40%, transparent)",
  interactiveDestructiveDefault: "var(--error-soft)",
  interactiveDestructiveHover: "var(--error-border)",
  interactiveDestructiveDisabled: "var(--color-surface-2)",
  interactiveDestructivePressed: "var(--error-border)",
  interactiveDestructiveAccentDefault: "var(--error-strong)",
  interactiveDestructiveAccentHover: "var(--error-hover)",
  interactiveDestructiveAccentPressed: "var(--error-hover)",
  interactiveDestructiveAccentDisabled:
    "color-mix(in oklab, var(--error-strong) 40%, transparent)",

  borderDefault: "var(--color-surface-border)",
  borderInteractive: "var(--color-surface-border-strong)",
  borderInteractiveEmphasis: "var(--color-surface-border-strong)",
  borderInteractiveSelected: "var(--color-brand)",
  borderAccent: "var(--color-brand)",
  borderAccentEmphasis: "var(--color-brand-600)",
  borderAccentSelected: "var(--color-brand-700)",
  borderInfo: "var(--info-border)",
  borderInfoEmphasis: "var(--color-brand)",
  borderAlert: "var(--warning-border)",
  borderAlertEmphasis: "var(--warning)",
  borderSuccess: "var(--success-border)",
  borderSuccessEmphasis: "var(--success)",
  borderDanger: "var(--error-border)",
  borderDangerEmphasis: "var(--error)",

  space000: "0px",
  space3xs: "4px",
  space2xs: "4px",
  spaceXs: "8px",
  spaceS: "8px",
  spaceSM: "12px",
  spaceM: "12px",
  spaceML: "16px",
  spaceL: "16px",
  spaceXl: "20px",
  space2xl: "24px",
  space3xl: "32px",
  radiusNone: "0px",
  radius3xs: "4px",
  radius2xs: "4px",
  radiusXs: "4px",
  radiusS: "4px",
  radiusM: "6px",
  radiusL: "8px",
  radiusXl: "10px",
  radius2xl: "12px",
  radius3xl: "12px",
  radius4xl: "12px",
  radius5xl: "12px",
  radius6xl: "12px",
  radius7xl: "12px",
  radius8xl: "12px",
  radius9xl: "12px",
  radiusFull: "9999px",

  fontBody:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontHeading:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontLabel:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontNumbers:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontCode: 'ui-monospace, "SF Mono", Menlo, monospace',
  fontSize2xs: "10px",
  fontSizeXs: "11px",
  fontSizeSm: "12px",
  fontSizeMd: "13px",
  fontSizeLg: "14px",
  fontSizeXl: "15px",
  fontSize2xl: "18px",
  fontSize3xl: "24px",
  fontSize4xl: "24px",
  fontSize5xl: "24px",
  fontWeightRegular: "400",
  fontWeightMedium: "500",
  fontWeightBold: "600",
  fontWeightHeavy: "600",
  letterSpacingNormal: "0",
  letterSpacingTight: "0",
  letterSpacingTighter: "0",

  shadow0: "none",
  shadowS: "var(--shadow-outline)",
  // OpenUI's card/popover primitives already draw a border. Valuz requires
  // bordered surfaces to use the ring-free outline shadow, avoiding a double edge.
  shadowM: "var(--shadow-outline)",
  shadowL: "var(--shadow-2)",
  shadowXl: "var(--shadow-3)",
  shadow2xl: "var(--shadow-4)",
  shadow3xl: "var(--shadow-4)",

  defaultChartPalette: chartPalette,
  barChartPalette: chartPalette,
  lineChartPalette: chartPalette,
  areaChartPalette: chartPalette,
  pieChartPalette: chartPalette,
  radarChartPalette: chartPalette,
  radialChartPalette: chartPalette,
  horizontalBarChartPalette: chartPalette,
};

/**
 * Extract the raw text payload from a kernel tool-output string.
 *
 * MCP tool results surface on the frontend wrapped in a JSON content-block
 * envelope — ``[{"type":"text","text":"<payload>"}]`` — because the host
 * toolkit MCP server returns ``TextContent`` and the kernel JSON-stringifies
 * the content blocks at the SSE boundary (``event_sse_adapter._stringify``).
 * Some runtimes also emit a Python-repr variant (``[{'type': 'text', ...}]``).
 * The OpenUI ``<Renderer>`` needs the inner text (the OpenUI Lang), not the
 * envelope, so unwrap both; fall through to the raw string when there's none.
 */
export function extractContentText(raw: string | undefined | null): string {
  const s = (raw ?? "").trim();
  if (!s) return "";

  // 1. JSON envelope (the common path).
  try {
    const parsed: unknown = JSON.parse(s);
    const text = readTextBlocks(parsed);
    if (text !== null) return text;
  } catch {
    /* not JSON — try repr / fall through */
  }

  // 2. Python-repr envelope from other runtimes: {'type': 'text', 'text': '…'}.
  const repr = matchReprText(s);
  if (repr !== null) return repr;

  // 3. No envelope — already raw text (OpenUI Lang passed through directly).
  return s;
}

function readTextBlocks(parsed: unknown): string | null {
  const entries = Array.isArray(parsed) ? parsed : [parsed];
  const texts: string[] = [];
  for (const e of entries) {
    if (
      e &&
      typeof e === "object" &&
      typeof (e as Record<string, unknown>).text === "string"
    ) {
      texts.push((e as Record<string, string>).text);
    }
  }
  if (texts.length) return texts.join("");
  if (typeof parsed === "string") return parsed; // double-stringified
  return null;
}

function matchReprText(s: string): string | null {
  // Match  'text': '…'  tolerating escaped quotes inside the value.
  const m = s.match(/'text'\s*:\s*'((?:[^'\\]|\\.)*)'/);
  if (!m || !m[1]) return null;
  return m[1]
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, "\\");
}

export interface GenerativeUICardProps {
  /** OpenUI Lang string — the generate_ui tool's output. */
  openui?: string;
  /** Tool status; "running" while the tool hasn't returned yet. */
  status?: "running" | "success" | "error";
}

const GENERATIVE_UI_LAYOUT_CSS = `
  [data-slot="generative-ui-card"]
    .openui-horizontal-bar-chart-container-inner-wrapper {
    height: auto !important;
    overflow: visible;
  }

  [data-slot="generative-ui-card"]
    .openui-horizontal-bar-chart-main-container {
    height: auto;
    overflow-y: visible;
  }
`;

/**
 * Renders the OpenUI Lang produced by the ``generate_ui`` MCP tool as live,
 * interactive components. Mounted inline via ``ConversationPage``'s
 * ``renderToolCall`` override (the same lift-out seam AskUserQuestion and
 * submit_skill use).
 */
export function GenerativeUICard({ openui, status }: GenerativeUICardProps) {
  const { t } = useI18n();
  const body = extractContentText(openui);

  return (
    <div
      data-slot="generative-ui-card"
      className="rounded-xl border border-surface-border bg-surface overflow-hidden"
    >
      <style>{GENERATIVE_UI_LAYOUT_CSS}</style>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-surface-border">
        <span className="text-sm font-medium text-ink-heading">
          {t("genui.cardTitle" as Parameters<typeof t>[0])}
        </span>
      </div>
      <div className="min-w-0 overflow-x-auto p-3 [&>*]:min-w-0 [&>*]:max-w-full">
        {body ? (
          <ThemeProvider
            lightTheme={VALUZ_OPENUUI_THEME}
            cssSelector="[data-slot='generative-ui-card']"
          >
            <Renderer
              library={openuiLibrary}
              response={body}
              isStreaming={status === "running"}
            />
          </ThemeProvider>
        ) : (
          <div
            data-testid="genui-empty"
            className="flex items-center gap-2 text-sm text-ink-meta"
          >
            {status === "running" ? (
              <>
                <Spinner className="size-3.5" />
                {t("genui.generating" as Parameters<typeof t>[0])}
              </>
            ) : (
              t("genui.empty" as Parameters<typeof t>[0])
            )}
          </div>
        )}
      </div>
    </div>
  );
}
