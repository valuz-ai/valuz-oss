"use client";

import { defineComponent } from "@openuidev/react-lang";

import { OptionCardSchema, OptionCardsSchema } from "./schema";

export { OptionCardSchema, OptionCardsSchema } from "./schema";

export const OptionCard = defineComponent({
  name: "OptionCard",
  props: OptionCardSchema,
  description:
    "One alternative in a set the reader is weighing up: a title and a short description of what choosing it means. " +
    "selected marks the recommended or already-chosen one — it is styling only, nothing happens when the card is clicked, so never promise the reader an action. " +
    "Always wrap a set of these in an OptionCards block; use TileOption when the choices are single words.",
  component: ({ props }) => (
    <div
      className="vgb-card vgb-option-card"
      data-slot="vgb-option-card"
      data-selected={props.selected ? "true" : undefined}
    >
      <span className="vgb-title vgb-option-card-title">{props.title}</span>
      {props.description ? <p className="vgb-body vgb-card-note">{props.description}</p> : null}
    </div>
  ),
});

export const OptionCards = defineComponent({
  name: "OptionCards",
  props: OptionCardsSchema,
  description:
    "Wrapping row of OptionCard. Reach for it whenever you lay out two or more alternatives — plans, strategies, routes, vendors — so they size evenly and wrap as the column narrows. " +
    "children is an array of OptionCard.",
  component: ({ props, renderNode }) => (
    <div className="vgb-block" data-slot="vgb-option-cards">
      {renderNode(props.children)}
    </div>
  ),
});
