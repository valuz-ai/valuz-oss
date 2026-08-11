import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  VALUZ_BASE_CATALOG_ID,
  ValuzA2UISurface,
  createValuzMessageProcessor,
  type A2uiMessage,
} from "../src";
import "../src/styles.css";
import "./styles.css";

const sampleImage =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='450'%3E%3Crect width='800' height='450' fill='lavender'/%3E%3Ccircle cx='400' cy='205' r='96' fill='mediumpurple' opacity='.75'/%3E%3Cpath d='M340 230l42-48 32 34 48-66 64 80z' fill='white' opacity='.92'/%3E%3C/svg%3E";

const surfaces = [
  makeSurface(
    "content",
    [
      { id: "root", component: "Stack", children: ["intro", "callout", "content-grid", "data-card"], gap: "lg" },
      { id: "intro", component: "Stack", children: ["eyebrow", "title", "summary", "tags"], gap: "sm" },
      { id: "eyebrow", component: "TextContent", text: "CONTENT & DATA", variant: "label", tone: "brand" },
      { id: "title", component: "TextContent", text: "A complete vocabulary for generated answers", variant: "h1" },
      { id: "summary", component: "Markdown", content: "The base catalog combines **structured data**, clear prose, media, feedback states, and reusable surfaces without app-specific assumptions.", compact: true },
      { id: "tags", component: "TagBlock", tags: [{ label: "A2UI v0.9.1", tone: "brand" }, { label: "51 components", tone: "success" }, { label: "Strict schemas", tone: "info" }] },
      { id: "callout", component: "Callout", title: "Machine-readable by design", content: "Every component description and property schema is exposed through the inline A2UI catalog.", tone: "info", icon: "insight" },
      { id: "content-grid", component: "Grid", children: ["media", "list-card", "progress-card", "code-card"], minItemWidth: 260, gap: "md" },
      { id: "media", component: "Card", title: "Accessible media", subtitle: "Image and gallery primitives", children: ["image"], padding: "sm" },
      { id: "image", component: "Image", src: sampleImage, alt: "Abstract purple landscape", caption: "Standalone data URI — no host dependency", aspectRatio: "video", radius: "md" },
      { id: "list-card", component: "Card", title: "Readable collections", children: ["list"], variant: "outlined" },
      { id: "list", component: "ListBlock", divided: true, items: [{ title: "Research", description: "Long-form exploration", icon: "search", value: "12" }, { title: "Evidence", description: "Traceable supporting items", icon: "document", value: "48" }, { title: "Decisions", description: "Actions and outcomes", icon: "complete", value: "5" }] },
      { id: "progress-card", component: "Card", title: "Feedback states", children: ["progress", "separator", "avatar"], variant: "muted" },
      { id: "progress", component: "Progress", label: "Catalog coverage", value: 86, tone: "success" },
      { id: "separator", component: "Separator", label: "Owner" },
      { id: "avatar", component: "Avatar", name: "Valuz A2UI", description: "Base catalog", shape: "rounded" },
      { id: "code-card", component: "Card", title: "Code and machine output", children: ["code"], padding: "none" },
      { id: "code", component: "CodeBlock", filename: "surface.json", language: "json", code: "{\n  \"component\": \"Card\",\n  \"children\": [\"body\"]\n}", showLineNumbers: true },
      { id: "data-card", component: "Card", title: "Structured comparison", children: ["table"], padding: "none" },
      { id: "table", component: "Table", caption: "Renderer quality gates", striped: true, columns: [{ key: "gate", label: "Gate" }, { key: "scope", label: "Scope" }, { key: "status", label: "Status", align: "right" }], rows: [{ gate: "Schema", scope: "51 APIs", status: "Passed" }, { gate: "Interaction", scope: "Actions + bindings", status: "Passed" }, { gate: "Visual", scope: "Light + dark", status: "Review" }] },
    ],
    {},
  ),
  makeSurface(
    "layout",
    [
      { id: "root", component: "Stack", children: ["title", "tabs", "layout-grid", "actions"], gap: "lg" },
      { id: "title", component: "TextContent", text: "Layout & interaction", variant: "h2" },
      { id: "tabs", component: "Tabs", variant: "pill", defaultValue: "overview", items: [{ label: "Overview", value: "overview", child: "tab-overview" }, { label: "Details", value: "details", child: "tab-details" }] },
      { id: "tab-overview", component: "TextContent", text: "Tabs reveal related views without changing the A2UI data model.", variant: "body" },
      { id: "tab-details", component: "Markdown", content: "Selection is **local UI state**. Actions remain explicit A2UI events.", compact: true },
      { id: "layout-grid", component: "Grid", children: ["steps-card", "accordion-card", "carousel-card"], minItemWidth: 280, gap: "md" },
      { id: "steps-card", component: "Card", title: "Steps", children: ["steps"] },
      { id: "steps", component: "Steps", items: [{ title: "Describe", description: "The agent chooses a component.", status: "complete" }, { title: "Generate", description: "The model emits A2UI JSON.", status: "complete" }, { title: "Render", description: "The catalog binds data and actions.", status: "current" }] },
      { id: "accordion-card", component: "Card", title: "Accordion", children: ["accordion"] },
      { id: "accordion", component: "Accordion", defaultOpen: [0], items: [{ title: "Why strict schemas?", description: "Catch invalid output at the boundary", child: "accordion-a" }, { title: "Why versioned catalogs?", child: "accordion-b" }] },
      { id: "accordion-a", component: "TextContent", text: "A model sees the same contract the renderer enforces.", variant: "body" },
      { id: "accordion-b", component: "TextContent", text: "Saved artifacts remain deterministic when future APIs evolve.", variant: "body" },
      { id: "carousel-card", component: "Card", title: "Carousel", children: ["carousel"] },
      { id: "carousel", component: "Carousel", children: ["slide-a", "slide-b", "slide-c"] },
      { id: "slide-a", component: "EmptyState", title: "First surface", description: "Compact, responsive and composable", icon: "sparkles" },
      { id: "slide-b", component: "EmptyState", title: "Second surface", description: "Keyboard accessible interactions", icon: "check" },
      { id: "slide-c", component: "Skeleton", variant: "text", lines: 4 },
      { id: "actions", component: "Stack", children: ["buttons", "followups"], gap: "md" },
      { id: "buttons", component: "ButtonGroup", children: ["primary", "outline", "ghost", "modal"] },
      { id: "primary", component: "Button", label: "Primary action", icon: "sparkles", action: { event: { name: "demo.primary" } } },
      { id: "outline", component: "Button", label: "Outline", variant: "outline", action: { event: { name: "demo.outline" } } },
      { id: "ghost", component: "Button", label: "Ghost", variant: "ghost", action: { event: { name: "demo.ghost" } } },
      { id: "modal", component: "Modal", triggerChild: "modal-trigger", contentChild: "modal-content", title: "Focused supplementary content", description: "Modal is part of the layout catalog." },
      { id: "modal-trigger", component: "Button", label: "Open modal", variant: "outline", action: { event: { name: "demo.modal" } } },
      { id: "modal-content", component: "Markdown", content: "This content is rendered from another component ID and remains inside the same surface." },
      { id: "followups", component: "FollowUpBlock", title: "Suggested next actions", layout: "grid", items: [{ label: "Inspect the catalog", description: "Review all strict component schemas", icon: "search", action: { event: { name: "demo.catalog" } } }, { label: "Create a domain catalog", description: "Compose on top of the base layer", icon: "next", action: { event: { name: "demo.extend" } } }] },
    ],
    {},
  ),
  makeSurface(
    "forms",
    [
      { id: "root", component: "Card", title: "Bound form controls", subtitle: "Every edit writes back to the official A2UI data model", children: ["form"] },
      { id: "form", component: "Form", children: ["form-grid", "toggles"], submitLabel: "Save settings", submit: { event: { name: "demo.submit", context: { query: { path: "/query" }, horizon: { path: "/horizon" } } } } },
      { id: "form-grid", component: "Grid", children: ["query", "notes", "region", "horizon", "coverage", "date", "confidence"], minItemWidth: 250, gap: "lg" },
      { id: "query", component: "Input", label: "Research topic", description: "Literal or bound string", value: { path: "/query" }, placeholder: "Describe what you want to study" },
      { id: "notes", component: "TextArea", label: "Context", value: { path: "/notes" }, rows: 3 },
      { id: "region", component: "Select", label: "Region", value: { path: "/region" }, options: [{ label: "Global", value: "global" }, { label: "United States", value: "us" }, { label: "Asia Pacific", value: "apac" }] },
      { id: "horizon", component: "RadioGroup", label: "Time horizon", value: { path: "/horizon" }, orientation: "horizontal", options: [{ label: "Quarter", value: "quarter" }, { label: "Year", value: "year" }, { label: "Long term", value: "long" }] },
      { id: "coverage", component: "CheckboxGroup", label: "Coverage", value: { path: "/coverage" }, orientation: "horizontal", options: [{ label: "Companies", value: "companies" }, { label: "Industries", value: "industries" }, { label: "Macro", value: "macro" }] },
      { id: "date", component: "DatePicker", label: "Review date", value: { path: "/date" } },
      { id: "confidence", component: "Slider", label: "Confidence threshold", value: { path: "/confidence" }, min: 0, max: 100, step: 5, unit: "%" },
      { id: "toggles", component: "Stack", children: ["switches", "segments"], direction: "horizontal", gap: "lg", align: "start" },
      { id: "switches", component: "SwitchGroup", label: "Notifications", value: { path: "/notifications" }, options: [{ label: "Material changes", description: "Only high-signal updates", value: "material" }, { label: "Weekly summary", description: "One digest every Friday", value: "weekly" }] },
      { id: "segments", component: "ToggleGroup", label: "Density", value: { path: "/density" }, options: [{ label: "Compact", value: "compact" }, { label: "Comfortable", value: "comfortable" }, { label: "Spacious", value: "spacious" }] },
    ],
    { query: "AI infrastructure", notes: "Focus on durable demand and supply constraints.", region: "global", horizon: "year", coverage: ["companies", "industries"], date: "2026-08-12", confidence: 70, notifications: ["material"], density: ["comfortable"] },
  ),
  makeSurface(
    "charts",
    [
      { id: "root", component: "Stack", children: ["title", "grid"], gap: "lg" },
      { id: "title", component: "TextContent", text: "Responsive visualization", variant: "h2" },
      { id: "grid", component: "Grid", children: ["line-card", "area-card", "bar-card", "hbar-card", "pie-card", "radar-card", "radial-card", "scatter-card"], minItemWidth: 360, gap: "lg" },
      { id: "line-card", component: "Card", children: ["line"], variant: "outlined" },
      { id: "line", component: "LineChart", title: "Line chart", description: "Two ordered series", data: { path: "/trend" }, xKey: "period", series: [{ key: "revenue", label: "Revenue" }, { key: "margin", label: "Margin" }], height: 240 },
      { id: "area-card", component: "Card", children: ["area"], variant: "outlined" },
      { id: "area", component: "AreaChart", title: "Area chart", data: { path: "/trend" }, xKey: "period", series: [{ key: "revenue", label: "Revenue" }], height: 240 },
      { id: "bar-card", component: "Card", children: ["bar"], variant: "outlined" },
      { id: "bar", component: "BarChart", title: "Bar chart", data: { path: "/trend" }, xKey: "period", series: [{ key: "revenue", label: "Revenue" }, { key: "cost", label: "Cost" }], height: 240 },
      { id: "hbar-card", component: "Card", children: ["hbar"], variant: "outlined" },
      { id: "hbar", component: "HorizontalBarChart", title: "Horizontal bar", data: { path: "/ranking" }, categoryKey: "name", series: [{ key: "score", label: "Score" }], height: 240, showLegend: false },
      { id: "pie-card", component: "Card", children: ["pie"], variant: "outlined" },
      { id: "pie", component: "DonutChart", title: "Donut chart", data: { path: "/mix" }, nameKey: "name", valueKey: "value", height: 240 },
      { id: "radar-card", component: "Card", children: ["radar"], variant: "outlined" },
      { id: "radar", component: "RadarChart", title: "Radar chart", data: { path: "/radar" }, categoryKey: "dimension", series: [{ key: "alpha", label: "Alpha" }, { key: "beta", label: "Beta" }], domainMax: 100, height: 240 },
      { id: "radial-card", component: "Card", children: ["radial"], variant: "outlined" },
      { id: "radial", component: "RadialChart", title: "Radial chart", data: { path: "/mix" }, nameKey: "name", valueKey: "value", height: 240 },
      { id: "scatter-card", component: "Card", children: ["scatter"], variant: "outlined" },
      { id: "scatter", component: "ScatterChart", title: "Scatter chart", data: { path: "/scatter" }, xKey: "growth", yKey: "margin", sizeKey: "scale", seriesName: "Companies", height: 240 },
    ],
    {
      trend: [{ period: "Q1", revenue: 32, cost: 21, margin: 11 }, { period: "Q2", revenue: 41, cost: 25, margin: 16 }, { period: "Q3", revenue: 38, cost: 22, margin: 16 }, { period: "Q4", revenue: 52, cost: 29, margin: 23 }],
      ranking: [{ name: "Compute", score: 92 }, { name: "Networking", score: 78 }, { name: "Storage", score: 64 }, { name: "Power", score: 58 }],
      mix: [{ name: "Compute", value: 44 }, { name: "Networking", value: 28 }, { name: "Storage", value: 18 }, { name: "Other", value: 10 }],
      radar: [{ dimension: "Growth", alpha: 84, beta: 62 }, { dimension: "Margin", alpha: 72, beta: 80 }, { dimension: "Durability", alpha: 91, beta: 70 }, { dimension: "Valuation", alpha: 55, beta: 76 }, { dimension: "Catalysts", alpha: 79, beta: 68 }],
      scatter: [{ growth: 12, margin: 22, scale: 40 }, { growth: 18, margin: 16, scale: 80 }, { growth: 24, margin: 31, scale: 120 }, { growth: 8, margin: 36, scale: 60 }, { growth: 29, margin: 12, scale: 100 }],
    },
  ),
];

function makeSurface(
  surfaceId: string,
  components: Record<string, unknown>[],
  data: Record<string, unknown>,
) {
  const processor = createValuzMessageProcessor((action) => {
    console.info("A2UI action", action);
  });
  const messages = [
    { version: "v0.9.1", createSurface: { surfaceId, catalogId: VALUZ_BASE_CATALOG_ID } },
    { version: "v0.9.1", updateDataModel: { surfaceId, path: "/", value: data } },
    { version: "v0.9.1", updateComponents: { surfaceId, components } },
  ] satisfies A2uiMessage[];
  processor.processMessages(messages);
  return processor.model.getSurface(surfaceId)!;
}

function Demo() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  return (
    <div className="demo-stage" data-theme={theme}>
      <main className="demo-shell">
        <header className="demo-header">
          <div>
            <span>VALUZ OPEN SOURCE</span>
            <h1>A2UI Base Catalog</h1>
            <p>Independent protocol runtime, strict component APIs, reusable React renderer.</p>
          </div>
          <div className="demo-header-actions">
            <code>{VALUZ_BASE_CATALOG_ID}</code>
            <button
              aria-pressed={theme === "dark"}
              onClick={() => setTheme((current) => current === "light" ? "dark" : "light")}
              type="button"
            >
              {theme === "light" ? "Dark theme" : "Light theme"}
            </button>
          </div>
        </header>
        <nav className="demo-nav" aria-label="Catalog sections">
          {surfaces.map((surface) => <a href={`#${surface.id}`} key={surface.id}>{surface.id}</a>)}
        </nav>
        {surfaces.map((surface) => (
          <section className="demo-section" id={surface.id} key={surface.id}>
            <ValuzA2UISurface surface={surface} theme={theme} />
          </section>
        ))}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Demo />
  </StrictMode>,
);
