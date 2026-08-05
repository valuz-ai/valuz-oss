"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readTextFromKeys } from "../lib/props";
import { MetricSchema } from "./schema";

export { MetricSchema } from "./schema";

/**
 * A bare label-over-value pair — no border, no background, no padding.
 *
 * That absence is the component: `Metric` is meant to sit *inside* something
 * that already draws a surface (a Card, a slide, a table cell), which is what
 * separates it from `ValueCard`. Swapping one for the other adds or removes a
 * frame, so they are not interchangeable even though their content matches.
 *
 * The `data-a2ui-*` attributes are a contract, not decoration: the host
 * stylesheet in `GenerativeUICard` keys on them.
 */
export const Metric = defineComponent({
  name: "Metric",
  props: MetricSchema,
  description:
    "A bare metric: its label above the figure, with no frame around it. Use it inside something that already draws a surface — a Card, a slide, a table cell — where a framed tile would nest one box inside another. " +
    "label is the metric name and value the already-formatted figure, unit included (\"$4.2M\", \"12.4%\"). " +
    "For a standalone metric that needs its own surface use MiniCard inside a MiniCardBlock, or StatsCard when one figure is the point of the section.",
  component: ({ props }) => {
    const record = props as Record<string, unknown>;
    const label = readTextFromKeys(record, ["label", "title"]);
    const value = readTextFromKeys(record, ["value", "text"]);
    return (
    <div
      data-slot="vgb-metric"
      data-a2ui-component="metric"
      style={{
        display: "flex",
        minWidth: 0,
        flexDirection: "column",
        gap: "var(--openui-space-2xs)",
      }}
    >
      {label ? (
        <span
          data-a2ui-metric-label
          style={{
            color: "var(--openui-text-neutral-secondary)",
            font: "var(--openui-text-label-sm)",
            letterSpacing: 0,
            overflowWrap: "anywhere",
          }}
        >
          {label}
        </span>
      ) : null}
      {value ? (
        <span
          data-a2ui-metric-value
          style={{
            color: "var(--openui-text-neutral-primary)",
            font: "var(--openui-text-numbers-heading-md)",
            fontVariantNumeric: "tabular-nums",
            letterSpacing: 0,
            overflowWrap: "anywhere",
          }}
        >
          {value}
        </span>
      ) : null}
      </div>
    );
  },
});
