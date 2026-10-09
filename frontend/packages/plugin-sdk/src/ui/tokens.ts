/**
 * The host's design tokens (CSS variables) a plugin's own CSS may use for
 * colours, radii, type and shadows, so it follows the light / dark theme.
 * ``tokens.colorPrimary`` is the string ``"var(--color-primary)"``.
 */
export const tokens = {
  // Surfaces and ink
  colorBackground: "var(--color-background)",
  colorForeground: "var(--color-foreground)",
  colorCard: "var(--color-card)",
  colorCardForeground: "var(--color-card-foreground)",
  colorPopover: "var(--color-popover)",
  colorPopoverForeground: "var(--color-popover-foreground)",
  colorMuted: "var(--color-muted)",
  colorMutedForeground: "var(--color-muted-foreground)",
  colorSurface: "var(--color-surface)",
  colorSurfaceSoft: "var(--color-surface-soft)",
  colorSurfaceMuted: "var(--color-surface-muted)",
  colorSurfaceBorder: "var(--color-surface-border)",
  colorInkHeading: "var(--color-ink-heading)",
  colorInkBody: "var(--color-ink-body)",
  colorInkLabel: "var(--color-ink-label)",
  colorInkMeta: "var(--color-ink-meta)",
  colorInkMuted: "var(--color-ink-muted)",
  colorInkDisabled: "var(--color-ink-disabled)",
  // Actions and state
  colorPrimary: "var(--color-primary)",
  colorPrimaryForeground: "var(--color-primary-foreground)",
  colorPrimaryHover: "var(--color-primary-hover)",
  colorAccent: "var(--color-accent)",
  colorAccentForeground: "var(--color-accent-foreground)",
  colorBrand: "var(--color-brand)",
  colorBrandLight: "var(--color-brand-light)",
  colorBorder: "var(--color-border)",
  colorInput: "var(--color-input)",
  colorRing: "var(--color-ring)",
  colorDestructive: "var(--color-destructive)",
  colorSuccess: "var(--color-success)",
  colorSuccessLight: "var(--color-success-light)",
  colorSuccessText: "var(--color-success-text)",
  colorWarning: "var(--color-warning)",
  colorWarningLight: "var(--color-warning-light)",
  colorWarningText: "var(--color-warning-text)",
  colorError: "var(--color-error)",
  colorErrorLight: "var(--color-error-light)",
  colorErrorText: "var(--color-error-text)",
  colorInfo: "var(--color-info)",
  colorInfoLight: "var(--color-info-light)",
  colorInfoText: "var(--color-info-text)",
  // Shape
  radiusSm: "var(--radius-sm)",
  radiusMd: "var(--radius-md)",
  radiusLg: "var(--radius-lg)",
  radiusXl: "var(--radius-xl)",
  radiusFull: "var(--radius-full)",
  // Type
  fontSans: "var(--font-sans)",
  fontMono: "var(--font-mono)",
  textXs: "var(--text-xs)",
  textSm: "var(--text-sm)",
  textBase: "var(--text-base)",
  textLg: "var(--text-lg)",
  textXl: "var(--text-xl)",
  // Depth
  shadowSm: "var(--shadow-sm)",
  shadowMd: "var(--shadow-md)",
  shadowPopover: "var(--shadow-popover)",
} as const;

export type TokenName = keyof typeof tokens;

/** The CSS variable names behind {@link tokens}, e.g. ``--color-primary``. */
export const tokenVariables: readonly string[] = Object.values(tokens).map(
  (value) => value.slice(4, -1),
);
