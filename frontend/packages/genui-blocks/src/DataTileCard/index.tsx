"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";
import { toneSurface, toneText } from "../lib/tone";
import { DataTileCardSchema } from "./schema";

export { DataTileCardSchema } from "./schema";

export const DataTileCard = defineComponent({
  name: "DataTileCard",
  props: DataTileCardSchema,
  description:
    "An amount over a one-line breakdown that qualifies it — \"$4.2M\" above \"across 12 accounts\". " +
    "Use it when the figure needs that second line to be understood; when it does not, MiniCard or ValueCard is lighter. " +
    "value is the formatted amount, breakdown the qualifying line, label an optional metric name above the value, and icon any lucide-react icon name — an unknown name simply renders no icon. " +
    "Lay several out with SmallCardBlock or MediumCardBlock.",
  component: ({ props }) => (
    <div className="vgb-tile vgb-data-tile" data-slot="vgb-data-tile-card">
      {props.icon ? (
        <span
          className="vgb-data-tile-icon"
          aria-hidden="true"
          style={{ backgroundColor: toneSurface(props.tone), color: toneText(props.tone) }}
        >
          <BlockIcon name={props.icon} size="55%" />
        </span>
      ) : null}
      <span className="vgb-data-tile-text">
        {props.label ? <span className="vgb-tile-label">{props.label}</span> : null}
        <span
          className="vgb-tile-value"
          style={props.tone ? { color: toneText(props.tone) } : undefined}
        >
          {props.value}
        </span>
        {props.breakdown ? <span className="vgb-data-tile-sub">{props.breakdown}</span> : null}
      </span>
    </div>
  ),
});
