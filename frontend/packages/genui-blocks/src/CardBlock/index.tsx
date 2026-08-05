"use client";

import { defineComponent } from "@openuidev/react-lang";

import { MediumCardBlockSchema, SmallCardBlockSchema } from "./schema";

export { MediumCardBlockSchema, SmallCardBlockSchema } from "./schema";

export const SmallCardBlock = defineComponent({
  name: "SmallCardBlock",
  props: SmallCardBlockSchema,
  description:
    "Wrapping row that gives each child a narrow column (about 11rem), so four or five fit across a wide chat column and they re-wrap as it narrows. " +
    "Use it for compact children — ValueCard, DataTileCard, ProfileTile — and switch to MediumCardBlock as soon as a child carries a paragraph. " +
    "children is an array of cards or tiles.",
  component: ({ props, renderNode }) => (
    <div className="vgb-card-block vgb-card-block-small" data-slot="vgb-small-card-block">
      {renderNode(props.children)}
    </div>
  ),
});

export const MediumCardBlock = defineComponent({
  name: "MediumCardBlock",
  props: MediumCardBlockSchema,
  description:
    "Wrapping row that gives each child a roomier column (about 18rem), leaving space for body text. " +
    "Use it for StatsCard, OverviewCard, ContextCard, CompositeCard, and VisualFirstCard; SmallCardBlock is the tighter option when the children are just a label and a figure. " +
    "children is an array of cards or tiles.",
  component: ({ props, renderNode }) => (
    <div className="vgb-card-block vgb-card-block-medium" data-slot="vgb-medium-card-block">
      {renderNode(props.children)}
    </div>
  ),
});
