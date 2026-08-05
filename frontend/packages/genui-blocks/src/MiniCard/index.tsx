"use client";

import { defineComponent } from "@openuidev/react-lang";

import { toneText, trendGlyph, trendTone } from "../lib/tone";
import { MiniCardBlockSchema, MiniCardSchema } from "./schema";

export { MiniCardBlockSchema, MiniCardSchema } from "./schema";

export const MiniCard = defineComponent({
  name: "MiniCard",
  props: MiniCardSchema,
  description:
    "A single compact metric: a label on the left, its value on the right. Use for KPI strips — revenue, growth rate, headcount, ratios. " +
    "label is the metric name, value the already-formatted figure (include the unit: \"$4.2M\", \"12.4%\"). " +
    "delta is an optional change figure shown beside the value, and trend (up|down|flat) colours it. " +
    "Always place MiniCards inside a MiniCardBlock — alone, one stretches to full width.",
  component: ({ props }) => {
    const tone = props.tone ?? trendTone(props.trend);
    return (
      <div className="vgb-tile vgb-tile-row" data-slot="vgb-mini-card">
        <span className="vgb-tile-slot-start">
          <span className="vgb-tile-label">{props.label}</span>
        </span>
        <span className="vgb-tile-slot-end" style={{ gap: "var(--openui-space-s)" }}>
          <span className="vgb-tile-value" style={{ fontSize: "var(--openui-font-size-lg)" }}>
            {props.value}
          </span>
          {props.delta ? (
            <span className="vgb-tile-delta" style={{ color: toneText(tone) }}>
              {props.trend ? <span aria-hidden="true">{trendGlyph(props.trend)}</span> : null}
              {props.delta}
            </span>
          ) : null}
        </span>
      </div>
    );
  },
});

export const MiniCardBlock = defineComponent({
  name: "MiniCardBlock",
  props: MiniCardBlockSchema,
  description:
    "Row of MiniCards that wraps as space runs out. This is the KPI strip: reach for it whenever you are about to show three or more single-number metrics side by side. " +
    "children is an array of MiniCard.",
  component: ({ props, renderNode }) => (
    <div className="vgb-block" data-slot="vgb-mini-card-block">
      {renderNode(props.children)}
    </div>
  ),
});
