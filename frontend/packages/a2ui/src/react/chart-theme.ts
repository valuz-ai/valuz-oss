import type { z } from "zod";

import {
  chartPaletteSchema,
  chartSeriesRoleSchema,
} from "../catalog/primitives";

export type ChartSeriesRole = z.infer<typeof chartSeriesRoleSchema>;
export type ChartPaletteName = z.infer<typeof chartPaletteSchema>;

/**
 * Exact palettes published by C1/OpenUI. Do not generate or hue-rotate these values.
 * Source: packages/react-ui/src/components/Charts/utils/PalletUtils.ts
 */
export const C1_CHART_PALETTES = {
  ocean: [
    "#0D47A1", "#1565C0", "#1976D2", "#1E88E5", "#2196F3", "#42A5F5",
    "#64B5F6", "#90CAF9", "#BBDEFB", "#E3F2FD", "#EFF8FF",
  ],
  orchid: [
    "#3A365B", "#482E77", "#552594", "#631DB0", "#7014CC", "#883BD5",
    "#A062DD", "#B88AE6", "#CFB1EE", "#E7D8F7", "#F7EFFF",
  ],
  emerald: [
    "#10451D", "#155D27", "#1A7431", "#208B3A", "#25A244", "#2DC653",
    "#4AD66D", "#6EDE8A", "#92E6A7", "#B7EFC5", "#DCFFE5",
  ],
  spectrum: [
    "#2171BC", "#2681D7", "#72A4EB", "#A0C0F7", "#C2D4F7", "#EADDE8",
    "#EEB3B1", "#E99492", "#E17475", "#D75259", "#CB253E",
  ],
  sunset: [
    "#0D0887", "#42049E", "#6A00A8", "#900DA4", "#B12A90", "#CC4678",
    "#E16462", "#F1844B", "#FCA636", "#FCCE25", "#FFE06E",
  ],
  vivid: [
    "#FF595E", "#FF924C", "#FFCA3A", "#C5CA30", "#8AC926", "#36949D",
    "#1982C4", "#4267AC", "#565AA0", "#6A4C93", "#63438F",
  ],
} as const;

/** Valuz additions use the same 11-color shape and C1 middle-out selection. */
export const VALUZ_CHART_PALETTES = {
  steel: [
    "#24303F", "#303E4F", "#3D4C5E", "#4A5B6D", "#586A7D", "#687B8F",
    "#7C8EA0", "#94A3B2", "#AEB9C4", "#CDD4DB", "#EDF0F3",
  ],
  amber: [
    "#542D00", "#6F3A00", "#874800", "#A35A00", "#BE6F00", "#D88700",
    "#E9A11A", "#F2B946", "#F8CF77", "#FBE3AD", "#FFF3D6",
  ],
} as const;

export const CHART_PALETTES = {
  ...C1_CHART_PALETTES,
  ...VALUZ_CHART_PALETTES,
} as const satisfies Record<ChartPaletteName, readonly string[]>;

const CHART_PALETTE_TOKENS = Object.fromEntries(
  Object.entries(CHART_PALETTES).map(([name, colors]) => [
    name,
    colors.map((color, index) => `var(--va2-chart-${name}-${index + 1}, ${color})`),
  ]),
) as Record<ChartPaletteName, string[]>;

/** C1's published middle-out color distribution algorithm from PalletUtils.ts. */
export function getDistributedChartColors(
  paletteName: ChartPaletteName,
  dataLength: number,
): string[] {
  if (dataLength <= 0) return [];
  const colors = CHART_PALETTE_TOKENS[paletteName];
  const middle = Math.floor(colors.length / 2);
  if (dataLength === 1) return [colors[middle]!];
  if (dataLength === 2) return [colors[middle - 1]!, colors[middle + 1]!];

  const offset = Math.floor((dataLength - 1) / 2);
  return Array.from({ length: dataLength }, (_, index) => {
    const paletteIndex = middle + index - offset;
    const wrappedIndex = ((paletteIndex % colors.length) + colors.length) % colors.length;
    return colors[wrappedIndex]!;
  });
}

export interface ChartSeriesVisual {
  areaGradientOpacity: number;
  color: string;
  fillOpacity: number;
  strokeDasharray?: string;
  strokeOpacity: number;
}

const semanticTokens: Record<ChartSeriesRole, string> = {
  actual: "var(--va2-chart-actual)",
  estimate: "var(--va2-chart-estimate)",
  benchmark: "var(--va2-chart-benchmark)",
  target: "var(--va2-chart-target)",
  positive: "var(--va2-chart-positive)",
  negative: "var(--va2-chart-negative)",
  total: "var(--va2-chart-total)",
  neutral: "var(--va2-chart-neutral)",
};

/** analytical/v1 series grammar shared by every chart implementation. */
export function resolveChartSeriesVisual(
  role: ChartSeriesRole | undefined,
  index: number,
  paletteColors: readonly string[] = getDistributedChartColors("ocean", 8),
): ChartSeriesVisual {
  const paletteColor = paletteColors.length > 0
    ? paletteColors[index % paletteColors.length]!
    : "var(--va2-chart-ocean-6, #42A5F5)";
  // `actual` is the primary data series rather than a fixed semantic state.
  // It follows the selected palette; estimate/benchmark/target and directional
  // roles keep their stable analytical meaning across palettes.
  const color = role && role !== "actual"
    ? semanticTokens[role]
    : paletteColor;

  if (role === "estimate") {
    return { areaGradientOpacity: 0.22, color, fillOpacity: 0.1, strokeDasharray: "6 4", strokeOpacity: 0.9 };
  }
  if (role === "benchmark") {
    return { areaGradientOpacity: 0.14, color, fillOpacity: 0.08, strokeDasharray: "3 3", strokeOpacity: 0.85 };
  }
  if (role === "target") {
    return { areaGradientOpacity: 0.14, color, fillOpacity: 0.08, strokeDasharray: "2 3", strokeOpacity: 0.9 };
  }
  if (role === "neutral") {
    return { areaGradientOpacity: 0.2, color, fillOpacity: 0.12, strokeOpacity: 0.82 };
  }
  return { areaGradientOpacity: 0.6, color, fillOpacity: 0.2, strokeOpacity: 1 };
}
