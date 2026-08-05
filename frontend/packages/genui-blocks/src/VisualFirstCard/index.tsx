"use client";

import { defineComponent } from "@openuidev/react-lang";

import { VisualFirstCardSchema } from "./schema";

export { VisualFirstCardSchema } from "./schema";

export const VisualFirstCard = defineComponent({
  name: "VisualFirstCard",
  props: VisualFirstCardSchema,
  description:
    "A card led by its image, with the title and body beneath. Use it when the picture carries the message — a product, a place, a screenshot, a piece of work — and skip it when you have no real image URL to show. " +
    "imageUrl must be a complete URL, imageAlt describes the picture for screen readers, and body is a sentence or two under the title. " +
    "Lay several out in a MediumCardBlock.",
  component: ({ props }) => (
    <div className="vgb-card vgb-visual-card" data-slot="vgb-visual-first-card">
      <img
        className="vgb-visual-card-media"
        src={props.imageUrl}
        alt={props.imageAlt ?? ""}
        loading="lazy"
      />
      <div className="vgb-visual-card-body">
        <h3 className="vgb-title vgb-card-title">{props.title}</h3>
        {props.body ? <p className="vgb-body">{props.body}</p> : null}
      </div>
    </div>
  ),
});
