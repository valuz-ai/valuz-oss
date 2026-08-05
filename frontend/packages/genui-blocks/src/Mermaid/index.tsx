"use client";

import { defineComponent } from "@openuidev/react-lang";

import { toneBorder, toneSurface, toneText } from "../lib/tone";
import { MermaidBadgeSchema, MermaidSchema } from "./schema";

export { MermaidBadgeSchema, MermaidSchema } from "./schema";

export const Mermaid = defineComponent({
  name: "Mermaid",
  props: MermaidSchema,
  description:
    "Displays the SOURCE of a Mermaid diagram in a labelled, scrollable code surface — this block shows the diagram definition as text, it does not draw the picture. " +
    "code is the Mermaid source, starting with its diagram keyword (\"flowchart TD\", \"sequenceDiagram\", \"gantt\") and including the newlines exactly as Mermaid expects them; title is an optional caption naming what the diagram shows. " +
    "Reach for it when the structure of a flow, sequence, or state machine is the answer; pair it with a MermaidBadge naming the diagram type.",
  component: ({ props }) => (
    // `data-vgb-mermaid` is the hydration hook: a host that ships a Mermaid
    // renderer can select `[data-vgb-mermaid]` and replace the `<pre>` inside
    // with a rendered diagram. This package deliberately carries no Mermaid
    // dependency, so the source is what renders here.
    <figure className="vgb-mermaid" data-slot="vgb-mermaid" data-vgb-mermaid="">
      <figcaption className="vgb-mermaid-caption">
        <span className="vgb-eyebrow">Mermaid diagram source</span>
        {props.title ? <span className="vgb-mermaid-title">{props.title}</span> : null}
      </figcaption>
      <div className="vgb-scroll-x">
        <pre className="vgb-mermaid-code">
          <code>{props.code}</code>
        </pre>
      </div>
    </figure>
  ),
});

export const MermaidBadge = defineComponent({
  name: "MermaidBadge",
  props: MermaidBadgeSchema,
  description:
    "A small badge naming a diagram's type or role — \"flowchart\", \"sequence\", \"state machine\", \"ERD\". Place it next to or above a Mermaid block so the reader knows what they are looking at before they read the source. " +
    "label is the badge text, kept to one or two words; tone is an optional colour role (neutral by default) — use info or brand to group related diagrams, danger only for a diagram of a failure path.",
  component: ({ props }) => (
    <span
      className="vgb-mermaid-badge"
      data-slot="vgb-mermaid-badge"
      style={{
        color: toneText(props.tone),
        backgroundColor: toneSurface(props.tone),
        borderColor: toneBorder(props.tone),
      }}
    >
      {props.label}
    </span>
  ),
});
