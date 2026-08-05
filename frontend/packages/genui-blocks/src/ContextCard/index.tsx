"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";

import { ContextCardSchema } from "./schema";

export { ContextCardSchema } from "./schema";

export const ContextCard = defineComponent({
  name: "ContextCard",
  props: ContextCardSchema,
  description:
    "Explanatory context to set beside a chart or a table: a title, the body text, and an optional source line for attribution (\"Source: Q3 filings, 2026\"). " +
    "Use it for the caveat, the methodology, or the definition a reader needs to trust the figure next to it — not for the finding itself. " +
    "source renders quietly at the foot of the card; leave it out when the data is the user's own." +
    "icon is any lucide-react icon name, shown as a small mark beside the heading — put it there rather than pasting an emoji into the text.",
  component: ({ props }) => (
    <div className="vgb-card vgb-context-card" data-slot="vgb-context-card">
      <span className="vgb-card-heading-row">
        <BlockIcon name={props.icon} className="vgb-card-icon" />
        <h3 className="vgb-title vgb-card-title">{props.title}</h3>
      </span>
      <p className="vgb-body">{props.body}</p>
      {props.source ? <p className="vgb-card-source">{props.source}</p> : null}
    </div>
  ),
});
