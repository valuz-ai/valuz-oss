import type { CSSProperties } from "react";

import type { Align, Size, Tone, Trend } from "./schema";

/**
 * Token lookups. Everything resolves to an `--openui-*` custom property so a
 * block never carries a literal colour — the host's `ThemeProvider` theme is
 * the single source of truth, and blocks restyle themselves when it changes.
 */

const TONE_TEXT: Record<Tone, string> = {
  neutral: "var(--openui-text-neutral-primary)",
  brand: "var(--openui-text-brand)",
  success: "var(--openui-text-success-primary)",
  warning: "var(--openui-text-alert-primary)",
  danger: "var(--openui-text-danger-primary)",
  info: "var(--openui-text-info-primary)",
};

const TONE_SURFACE: Record<Tone, string> = {
  neutral: "var(--openui-highlight-subtle)",
  brand: "var(--openui-highlight)",
  success: "var(--openui-success-background)",
  warning: "var(--openui-alert-background)",
  danger: "var(--openui-danger-background)",
  info: "var(--openui-info-background)",
};

const TONE_BORDER: Record<Tone, string> = {
  neutral: "var(--openui-border-default)",
  brand: "var(--openui-border-accent)",
  success: "var(--openui-border-success)",
  warning: "var(--openui-border-alert)",
  danger: "var(--openui-border-danger)",
  info: "var(--openui-border-info)",
};

export function toneText(tone: Tone | undefined): string {
  return TONE_TEXT[tone ?? "neutral"];
}

export function toneSurface(tone: Tone | undefined): string {
  return TONE_SURFACE[tone ?? "neutral"];
}

export function toneBorder(tone: Tone | undefined): string {
  return TONE_BORDER[tone ?? "neutral"];
}

/**
 * Trend colour, in the Greater China convention: **up is red, down is green.**
 *
 * This is the opposite of the Western convention and it is not a preference —
 * to a reader of A-share markets, a green number means a fall. Getting it
 * backwards does not look like a styling bug, it looks like the opposite
 * number.
 *
 * This function is the single place the convention is decided for every block.
 * The rest of the repo already agrees with it: the host stylesheet colours
 * OpenUI's `openui-tag-success` (a rise) with `--error-text`. `market.css`
 * repeats the mapping in CSS for elements that carry `data-a2ui-trend` rather
 * than an inline colour — change both together, or a card and the tile beside
 * it will disagree.
 *
 * A host serving Western markets inverts it here, once.
 */
export function trendTone(trend: Trend | undefined): Tone {
  if (trend === "up") return "danger";
  if (trend === "down") return "success";
  return "neutral";
}

export function trendGlyph(trend: Trend | undefined): string {
  if (trend === "up") return "▲";
  if (trend === "down") return "▼";
  return "—";
}

/** Type scale, one step larger on slide surfaces than in inline blocks. */
const INLINE_TYPE: Record<Size, string> = {
  small: "var(--openui-font-size-sm)",
  medium: "var(--openui-font-size-lg)",
  large: "var(--openui-font-size-2xl)",
};

const SLIDE_TYPE: Record<Size, string> = {
  small: "var(--openui-font-size-lg)",
  medium: "var(--openui-font-size-2xl)",
  large: "var(--openui-font-size-4xl)",
};

export function typeScale(size: Size | undefined, surface: "inline" | "slide" = "inline"): string {
  const table = surface === "slide" ? SLIDE_TYPE : INLINE_TYPE;
  return table[size ?? "medium"];
}

export function alignStyle(align: Align | undefined): CSSProperties {
  const value = align ?? "left";
  return {
    textAlign: value,
    alignItems: value === "center" ? "center" : value === "right" ? "flex-end" : "flex-start",
  };
}
