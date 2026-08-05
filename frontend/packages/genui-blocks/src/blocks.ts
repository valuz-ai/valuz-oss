import type { ComponentGroup, DefinedComponent } from "@openuidev/react-lang";

import { MediumCardBlock, SmallCardBlock } from "./CardBlock";
import { Citation, CondensedSources, SourceItem, SourceList } from "./Citation";
import { CompositeCard } from "./CompositeCard";
import { ContextCard } from "./ContextCard";
import { DataList, DataListItem } from "./DataList";
import { DataTileCard } from "./DataTileCard";
import { IconTag, IconText } from "./IconTag";
import { MarketBreadth } from "./MarketBreadth";
import { MarketIndexCard, MarketIndexGrid } from "./MarketIndexGrid";
import { Mermaid, MermaidBadge } from "./Mermaid";
import { Metric } from "./Metric";
import { MiniCard, MiniCardBlock } from "./MiniCard";
import { OptionCard, OptionCards } from "./OptionCard";
import { OverviewCard } from "./OverviewCard";
import { ProfileTile } from "./ProfileTile";
import {
  ReportDocument,
  ReportFrontPage,
  ReportHeadline,
  ReportImage,
  ReportKeyStatement,
  ReportPage,
  ReportSection,
  ReportTable,
  ReportTocPage,
} from "./Report";
import { StatsCard } from "./StatsCard";
import { TileOption, TileOptionBlock } from "./TileOption";
import { ValueCard } from "./ValueCard";
import { VisualFirstCard } from "./VisualFirstCard";

/**
 * A block of any shape.
 *
 * `defineComponent` parameterises its return type by the component's own
 * schema, and the renderer inside is contravariant in props — so a
 * `DefinedComponent<typeof MiniCardSchema>` is *not* assignable to the
 * default-parameter `DefinedComponent`, and a heterogeneous registry cannot be
 * typed with it. `createLibrary` hits the same wall and resolves it the same
 * way (`DefinedComponent<any, C>[]`); matching upstream keeps this list
 * expressible without casting at every element.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type BlockComponent = DefinedComponent<any>;

/**
 * The registry. Every block must appear in both lists below: `blockComponents`
 * makes it renderable, `blockComponentGroups` makes the model aware it exists.
 * A component missing from the groups still renders if the model somehow emits
 * it, but nothing will ever tell the model to.
 */

export const blockComponents: BlockComponent[] = [
  // Metric tiles
  MiniCard,
  MiniCardBlock,
  Metric,
  IconTag,
  IconText,
  // Cards & tiles
  SmallCardBlock,
  MediumCardBlock,
  StatsCard,
  DataTileCard,
  ValueCard,
  OverviewCard,
  ContextCard,
  CompositeCard,
  VisualFirstCard,
  ProfileTile,
  OptionCards,
  OptionCard,
  TileOptionBlock,
  TileOption,
  // Citations & sources
  Citation,
  CondensedSources,
  SourceList,
  SourceItem,
  // Report pages
  ReportDocument,
  ReportPage,
  ReportFrontPage,
  ReportTocPage,
  ReportSection,
  ReportHeadline,
  ReportKeyStatement,
  ReportTable,
  ReportImage,
  // Market data
  MarketIndexGrid,
  MarketIndexCard,
  MarketBreadth,
  DataList,
  DataListItem,
  // Diagrams
  Mermaid,
  MermaidBadge,
];

export const blockComponentGroups: ComponentGroup[] = [
  {
    name: "Metric Tiles",
    components: ["MiniCardBlock", "MiniCard", "Metric", "IconTag", "IconText"],
    notes: [
      "Prefer MiniCardBlock over a row of Cards whenever every entry is a single label + figure; Metric is the unframed version, for use inside a surface that already has a frame. IconTag marks something with a lucide icon, and IconText pairs one with a line of text.",
    ],
  },
  {
    name: "Cards & Tiles",
    components: [
      "SmallCardBlock",
      "MediumCardBlock",
      "StatsCard",
      "DataTileCard",
      "ValueCard",
      "OverviewCard",
      "ContextCard",
      "CompositeCard",
      "VisualFirstCard",
      "ProfileTile",
      "OptionCards",
      "OptionCard",
      "TileOptionBlock",
      "TileOption",
    ],
    notes: [
      "Lay card sets out with SmallCardBlock (children are a label plus a figure) or MediumCardBlock (children carry body text); OptionCard and TileOption must sit inside OptionCards / TileOptionBlock, and their selected state is styling only — nothing is clickable.",
    ],
  },
  {
    name: "Citations & Sources",
    components: ["Citation", "CondensedSources", "SourceList", "SourceItem"],
    notes: [
      "Attach a Citation to every claim taken from a source, then close the answer with CondensedSources — reach for SourceList only when the sources are themselves part of the argument.",
    ],
  },
  {
    name: "Report Pages",
    components: [
      "ReportDocument",
      "ReportPage",
      "ReportFrontPage",
      "ReportTocPage",
      "ReportSection",
      "ReportHeadline",
      "ReportKeyStatement",
      "ReportTable",
      "ReportImage",
    ],
    notes: [
      "Long-form printable documents: ReportDocument wraps ReportFrontPage, an optional ReportTocPage, then one ReportPage per page — fill pages with the ordinary shared blocks (MiniCardBlock, TextContent, charts), never a report-specific twin of them.",
    ],
  },
  {
    name: "Market Data",
    components: [
      "MarketIndexGrid",
      "MarketIndexCard",
      "MarketBreadth",
      "DataList",
      "DataListItem",
    ],
    notes: [
      "Market answers open with a MarketIndexGrid of quotes and, when the answer claims the move was broad, a MarketBreadth beside it; DataList is the ranking/leaderboard shape and beats a Table whenever every row is name + figure + change.",
    ],
  },
  {
    name: "Diagrams",
    components: ["Mermaid", "MermaidBadge"],
    notes: [
      "Mermaid shows the diagram definition as text, not a drawn picture — always label it with a MermaidBadge naming the diagram type.",
    ],
  },
];
