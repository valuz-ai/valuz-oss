import "./styles.css";

export type { BlockComponent } from "./blocks";
export type { BlockPropSpec, BlockSpec } from "./catalog";
export {
  blockCatalog,
  blockNames,
  describeBlock,
  renderBlockCatalogText,
} from "./catalog";
export { blockAdditionalRules, blockExamples, valuzPromptOptions } from "./prompt";
export {
  createBlockOnlyLibrary,
  createValuzLibrary,
  blockComponentGroups,
  blockComponents,
} from "./library";

// Metric tiles
export * from "./MiniCard";
export * from "./Metric";
export * from "./IconTag";

// Cards & tiles
export * from "./CardBlock";
export * from "./CompositeCard";
export * from "./ContextCard";
export * from "./DataTileCard";
export * from "./OptionCard";
export * from "./OverviewCard";
export * from "./ProfileTile";
export * from "./StatsCard";
export * from "./TileOption";
export * from "./ValueCard";
export * from "./VisualFirstCard";

// Citations & sources
export * from "./Citation";

// Report documents
export * from "./Report";

// Market data
export * from "./DataList";
export * from "./MarketIndexGrid";
export * from "./MarketBreadth";

// Diagrams
export * from "./Mermaid";
