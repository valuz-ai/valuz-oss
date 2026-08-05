"use client";

import { defineComponent } from "@openuidev/react-lang";

import { TileOptionBlockSchema, TileOptionSchema } from "./schema";

export { TileOptionBlockSchema, TileOptionSchema } from "./schema";

export const TileOption = defineComponent({
  name: "TileOption",
  props: TileOptionSchema,
  description:
    "The compact option: a short label — a word or a phrase — with at most a one-line description under it. " +
    "selected marks the chosen one and is styling only; these tiles are not buttons and clicking does nothing. " +
    "Use OptionCard instead when each choice needs a sentence to explain it, and always wrap a set in a TileOptionBlock.",
  component: ({ props }) => (
    <div
      className="vgb-tile vgb-tile-option"
      data-slot="vgb-tile-option"
      data-selected={props.selected ? "true" : undefined}
    >
      <span className="vgb-tile-option-label">{props.label}</span>
      {props.description ? (
        <span className="vgb-tile-option-note">{props.description}</span>
      ) : null}
    </div>
  ),
});

export const TileOptionBlock = defineComponent({
  name: "TileOptionBlock",
  props: TileOptionBlockSchema,
  description:
    "Wrapping row of TileOption — the compact counterpart to OptionCards. Use it for short, scannable choices such as timeframes, regions, or tiers. " +
    "children is an array of TileOption.",
  component: ({ props, renderNode }) => (
    <div className="vgb-block" data-slot="vgb-tile-option-block">
      {renderNode(props.children)}
    </div>
  ),
});
