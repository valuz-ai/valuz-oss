"use client";

import { defineComponent } from "@openuidev/react-lang";

import { ValueCardSchema } from "./schema";

export { ValueCardSchema } from "./schema";

export const ValueCard = defineComponent({
  name: "ValueCard",
  props: ValueCardSchema,
  description:
    "The plainest value display: label, then value, then an optional description, stacked. " +
    "Use it for attributes rather than metrics — owner, stage, region, status — inside a SmallCardBlock so a set of them forms a quiet grid. " +
    "For a headline metric reach for StatsCard, and for a figure that needs a qualifying line reach for DataTileCard.",
  component: ({ props }) => (
    <div className="vgb-card vgb-value-card" data-slot="vgb-value-card">
      <span className="vgb-tile-label">{props.label}</span>
      <span className="vgb-tile-value vgb-value-card-value">{props.value}</span>
      {props.description ? <p className="vgb-body vgb-card-note">{props.description}</p> : null}
    </div>
  ),
});
