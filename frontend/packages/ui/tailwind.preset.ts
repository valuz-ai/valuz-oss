// Design token source-of-truth. Mirrors the CSS custom properties declared
// in src/styles/workspace.css (which is what Tailwind v4 actually consumes
// at build time via `@import "tailwindcss"` + `@theme`). This TS export lets
// app code reference the same tokens type-safely.
//
// NOTE: Tailwind v4 drops JS presets in favor of CSS `@theme`. Apps do not
// merge this object into a `tailwind.config.ts`; they import the stylesheet
// from `@valuz/ui` (which re-exports workspace.css via the package entry).
// This file therefore serves as the canonical token registry for non-CSS
// consumers (charts, inline styles, docs).

export const tokens = {
  color: {
    background: '#f8f9fb',
    foreground: '#131313',
    primary: '#6d5cff',
    primaryForeground: '#ffffff',
    mutedForeground: '#6e7481',
    border: '#e6e7e9',
    card: '#ffffff',
    surface: '#ffffff',
    surfaceSoft: '#f7f8fa',
    surfaceMuted: '#f3f4f6',
    brand: '#6d5cff',
    brandHover: '#5b4be0',
    brandLight: '#ede9ff',
    success: '#16a34a',
    warning: '#d97706',
    error: '#dc2626',
  },
  font: {
    sans: '"PingFang SC"',
    serif: '"PingFang SC"',
    mono: 'monospace',
  },
  radius: {
    sm: '2px',
    md: '6px',
    lg: '8px',
    xl: '10px',
    '2xl': '12px',
    '3xl': '14px',
    full: '9999px',
  },
} as const

export type DesignTokens = typeof tokens

export default tokens
