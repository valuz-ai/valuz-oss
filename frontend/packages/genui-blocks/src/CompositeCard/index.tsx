"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";

import { CompositeCardSchema } from "./schema";

export { CompositeCardSchema } from "./schema";

export const CompositeCard = defineComponent({
  name: "CompositeCard",
  props: CompositeCardSchema,
  description:
    "A heading area stacked over a free children slot: eyebrow (a short kicker like \"Q3\" or \"NORTH AMERICA\") above the title, with value as a formatted figure trailing on the right. " +
    "Reach for it when a section needs both a headline number and nested content — a chart, a Table, a MiniCardBlock — under one roof; use OverviewCard when a plain title and paragraph will do. " +
    "clickable only adds hover styling: these cards never navigate anywhere, so do not describe them as buttons." +
    "icon is any lucide-react icon name, shown as a small mark beside the heading — put it there rather than pasting an emoji into the text.",
  component: ({ props, renderNode }) => (
    <div
      className={`vgb-card vgb-composite-card${props.clickable ? " vgb-card-clickable" : ""}`}
      data-slot="vgb-composite-card"
    >
      <div className="vgb-card-head">
        <span className="vgb-card-head-text">
          {props.eyebrow ? <span className="vgb-eyebrow">{props.eyebrow}</span> : null}
          <span className="vgb-card-heading-row">
            <BlockIcon name={props.icon} className="vgb-card-icon" />
            <h3 className="vgb-title vgb-card-title">{props.title}</h3>
          </span>
        </span>
        {props.value ? <span className="vgb-tile-value vgb-card-lead">{props.value}</span> : null}
      </div>
      {props.children ? (
        <div className="vgb-card-slot">{renderNode(props.children)}</div>
      ) : null}
    </div>
  ),
});
