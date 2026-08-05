"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";

import { toneText, trendGlyph, trendTone } from "../lib/tone";
import { StatsCardSchema } from "./schema";

export { StatsCardSchema } from "./schema";

export const StatsCard = defineComponent({
  name: "StatsCard",
  props: StatsCardSchema,
  description:
    "One headline figure with room to breathe: the metric label, the figure set large, and an optional sentence explaining it. " +
    "Reach for this when a single number *is* the point of the section — use MiniCard inside a MiniCardBlock instead once three or more figures sit side by side. " +
    "value is already formatted with its unit (\"$4.2M\", \"12.4%\"), delta is the change figure, trend (up|down|flat) colours it, and description is one short supporting sentence. " +
    "Put several in a MediumCardBlock to lay them out as a row." +
    "icon is any lucide-react icon name, shown as a small mark beside the heading — put it there rather than pasting an emoji into the text.",
  component: ({ props }) => {
    const deltaTone = props.tone ?? trendTone(props.trend);
    return (
      <div className="vgb-card vgb-stat-card" data-slot="vgb-stats-card">
        <span className="vgb-card-heading-row">
          <BlockIcon name={props.icon} className="vgb-card-icon" />
          <span className="vgb-tile-label">{props.label}</span>
        </span>
        <div className="vgb-stat-figure">
          <span
            className="vgb-tile-value vgb-stat-value"
            style={props.tone ? { color: toneText(props.tone) } : undefined}
          >
            {props.value}
          </span>
          {props.delta ? (
            <span className="vgb-tile-delta" style={{ color: toneText(deltaTone) }}>
              {props.trend ? <span aria-hidden="true">{trendGlyph(props.trend)}</span> : null}
              {props.delta}
            </span>
          ) : null}
        </div>
        {props.description ? <p className="vgb-body vgb-card-note">{props.description}</p> : null}
      </div>
    );
  },
});
