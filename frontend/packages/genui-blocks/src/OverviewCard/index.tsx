"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";

import { OverviewCardSchema } from "./schema";

export { OverviewCardSchema } from "./schema";

export const OverviewCard = defineComponent({
  name: "OverviewCard",
  props: OverviewCardSchema,
  description:
    "The general-purpose section summary: a title, a short body paragraph, then whatever the section contains in the children slot — a MiniCardBlock, a Table, a chart. " +
    "Reach for it to introduce a group of blocks instead of leaving them floating unlabelled. " +
    "Keep body to a sentence or two; if the card needs a headline figure beside the title use CompositeCard instead." +
    "icon is any lucide-react icon name, shown as a small mark beside the heading — put it there rather than pasting an emoji into the text.",
  component: ({ props, renderNode }) => (
    <div className="vgb-card vgb-overview-card" data-slot="vgb-overview-card">
      <span className="vgb-card-heading-row">
        <BlockIcon name={props.icon} className="vgb-card-icon" />
        <h3 className="vgb-title vgb-card-title">{props.title}</h3>
      </span>
      {props.body ? <p className="vgb-body">{props.body}</p> : null}
      {props.children ? (
        <div className="vgb-card-slot">{renderNode(props.children)}</div>
      ) : null}
    </div>
  ),
});
