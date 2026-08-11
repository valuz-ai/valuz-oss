import {
  VALUZ_BASE_CATALOG_ID,
  createValuzMessageProcessor,
  type A2uiMessage,
} from "../src";

export type GalleryCategoryId = "layout" | "content" | "actions" | "forms" | "charts";

export interface GallerySpecimen {
  name: string;
  description: string;
  componentNames: string[];
  surface: NonNullable<ReturnType<ReturnType<typeof createValuzMessageProcessor>["model"]["getSurface"]>>;
}

export interface GalleryCategory {
  id: GalleryCategoryId;
  label: string;
  eyebrow: string;
  description: string;
  specimens: GallerySpecimen[];
}

type ComponentNode = Record<string, unknown> & { id: string; component: string };

const sampleImage =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='450'%3E%3Crect width='800' height='450' fill='lavender'/%3E%3Ccircle cx='400' cy='205' r='96' fill='mediumpurple' opacity='.75'/%3E%3Cpath d='M340 230l42-48 32 34 48-66 64 80z' fill='white' opacity='.92'/%3E%3C/svg%3E";

function specimen(
  name: string,
  description: string,
  components: ComponentNode[],
  data: Record<string, unknown> = {},
): GallerySpecimen {
  const surfaceId = `gallery-${name.toLowerCase()}`;
  const processor = createValuzMessageProcessor();
  processor.processMessages([
    { version: "v0.9.1", createSurface: { surfaceId, catalogId: VALUZ_BASE_CATALOG_ID } },
    { version: "v0.9.1", updateDataModel: { surfaceId, path: "/", value: data } },
    { version: "v0.9.1", updateComponents: { surfaceId, components } },
  ] satisfies A2uiMessage[]);
  return {
    name,
    description,
    componentNames: components.map((component) => component.component),
    surface: processor.model.getSurface(surfaceId)!,
  };
}

const trend = [
  { period: "Q1", revenue: 32, cost: 21, margin: 18, capacity: 42, demand: 36 },
  { period: "Q2", revenue: 41, cost: 25, margin: 21, capacity: 45, demand: 43 },
  { period: "Q3", revenue: 38, cost: 22, margin: 20, capacity: 48, demand: 51 },
  { period: "Q4", revenue: 52, cost: 29, margin: 24, capacity: 54, demand: 58 },
];
const mix = [
  { name: "Compute", value: 44 },
  { name: "Networking", value: 28 },
  { name: "Storage", value: 18 },
  { name: "Other", value: 10 },
];
const ranking = [
  { name: "Compute", score: 92 },
  { name: "Networking", score: 78 },
  { name: "Storage", score: 64 },
  { name: "Power", score: 58 },
];

const layoutSpecimens = [
  specimen("Stack", "横向或纵向组织内容，并控制间距、对齐和换行。", [
    { id: "root", component: "Stack", children: ["a", "b", "c"], direction: "horizontal", gap: "sm", wrap: true },
    { id: "a", component: "TagBlock", tags: [{ label: "Research" }] },
    { id: "b", component: "TagBlock", tags: [{ label: "Evidence", tone: "info" }] },
    { id: "c", component: "TagBlock", tags: [{ label: "Decision", tone: "success" }] },
  ]),
  specimen("Grid", "根据可用宽度自动换列的响应式网格。", [
    { id: "root", component: "Grid", children: ["a", "b", "c"], minItemWidth: 120, gap: "sm" },
    { id: "a", component: "Card", title: "Alpha", children: ["ta"], variant: "muted", padding: "sm" },
    { id: "ta", component: "TextContent", text: "12.4%", variant: "h3" },
    { id: "b", component: "Card", title: "Beta", children: ["tb"], variant: "muted", padding: "sm" },
    { id: "tb", component: "TextContent", text: "0.82", variant: "h3" },
    { id: "c", component: "Card", title: "Coverage", children: ["tc"], variant: "muted", padding: "sm" },
    { id: "tc", component: "TextContent", text: "18", variant: "h3" },
  ]),
  specimen("Card", "承载一组有明确边界的标题、说明和内容。", [
    { id: "root", component: "Card", title: "Research snapshot", subtitle: "Updated moments ago", children: ["body", "tags"] },
    { id: "body", component: "Markdown", content: "Demand remains **strong**, while supply is becoming the key constraint.", compact: true },
    { id: "tags", component: "TagBlock", tags: [{ label: "High signal", tone: "success" }, { label: "Monitoring", tone: "info" }] },
  ]),
  specimen("Tabs", "在同一个位置切换相关视图，选择状态保留在界面本地。", [
    { id: "root", component: "Tabs", defaultValue: "summary", variant: "pill", items: [{ label: "Summary", value: "summary", child: "summary" }, { label: "Evidence", value: "evidence", child: "evidence" }] },
    { id: "summary", component: "TextContent", text: "The thesis remains intact.", variant: "body" },
    { id: "evidence", component: "TextContent", text: "Three new supporting signals arrived.", variant: "body" },
  ]),
  specimen("Accordion", "渐进披露较长内容，支持单项或多项展开。", [
    { id: "root", component: "Accordion", defaultOpen: [0], items: [{ title: "Core assumption", description: "What must remain true", child: "a" }, { title: "Primary risk", child: "b" }] },
    { id: "a", component: "TextContent", text: "Enterprise AI workloads continue to expand.", variant: "body" },
    { id: "b", component: "TextContent", text: "Capacity additions arrive faster than demand.", variant: "body" },
  ]),
  specimen("Steps", "表达有顺序的流程、阶段和完成状态。", [
    { id: "root", component: "Steps", items: [{ title: "Collect", description: "Gather primary evidence", status: "complete" }, { title: "Evaluate", description: "Update the thesis", status: "current" }, { title: "Decide", description: "Choose the next action", status: "pending" }] },
  ]),
  specimen("Carousel", "在有限空间中逐项浏览相关内容。", [
    { id: "root", component: "Carousel", children: ["a", "b", "c"] },
    { id: "a", component: "EmptyState", title: "First view", description: "Research summary", icon: "sparkles" },
    { id: "b", component: "EmptyState", title: "Second view", description: "Evidence update", icon: "document" },
    { id: "c", component: "EmptyState", title: "Third view", description: "Recommended action", icon: "next" },
  ]),
  specimen("Separator", "分隔相邻内容，可选水平、垂直和带标签样式。", [
    { id: "root", component: "Separator", label: "Supporting evidence" },
  ]),
  specimen("Modal", "在当前上下文上方展示需要集中处理的补充内容。", [
    { id: "root", component: "Modal", triggerChild: "trigger", contentChild: "content", title: "Evidence details", description: "A focused supplementary surface." },
    { id: "trigger", component: "Button", label: "Open modal", variant: "outline", action: { event: { name: "gallery.modal" } } },
    { id: "content", component: "Markdown", content: "This content remains part of the same A2UI surface." },
  ]),
];

const contentSpecimens = [
  specimen("TextContent", "覆盖展示标题、正文、标签和语义色文本。", [
    { id: "root", component: "Stack", children: ["label", "title", "body"], gap: "xs" },
    { id: "label", component: "TextContent", text: "LATEST VIEW", variant: "label", tone: "brand" },
    { id: "title", component: "TextContent", text: "A durable research conclusion", variant: "h2" },
    { id: "body", component: "TextContent", text: "Clear hierarchy makes generated content easier to scan.", variant: "body" },
  ]),
  specimen("Markdown", "渲染安全 Markdown，包括段落、列表、强调和链接。", [
    { id: "root", component: "Markdown", content: "### Research note\n\n- Demand accelerated\n- Margin expanded\n- **Thesis confidence increased**" },
  ]),
  specimen("Image", "展示单张图片，并提供比例、裁切、圆角和说明。", [
    { id: "root", component: "Image", src: sampleImage, alt: "Abstract purple landscape", caption: "Generated visual · 16:9", aspectRatio: "video" },
  ]),
  specimen("ImageGallery", "以响应式网格组织一组相关图片。", [
    { id: "root", component: "ImageGallery", columns: 3, aspectRatio: "square", images: [{ src: sampleImage, alt: "Landscape one", caption: "Overview" }, { src: sampleImage, alt: "Landscape two", caption: "Detail" }, { src: sampleImage, alt: "Landscape three", caption: "Comparison" }] },
  ]),
  specimen("TagBlock", "表达分类、状态、筛选条件和轻量元数据。", [
    { id: "root", component: "TagBlock", size: "md", tags: [{ label: "AI infrastructure", tone: "brand" }, { label: "High conviction", tone: "success" }, { label: "Risk review", tone: "warning" }] },
  ]),
  specimen("ListBlock", "结构化展示图标、标题、说明和值。", [
    { id: "root", component: "ListBlock", divided: true, items: [{ title: "Research", description: "Long-form exploration", icon: "search", value: "12" }, { title: "Evidence", description: "Traceable supporting items", icon: "document", value: "48" }, { title: "Decisions", description: "Actions and outcomes", icon: "complete", value: "5" }] },
  ]),
  specimen("Table", "以列定义渲染可比较的结构化数据。", [
    { id: "root", component: "Table", caption: "Coverage quality", striped: true, columns: [{ key: "company", label: "Company" }, { key: "score", label: "Score", align: "right" }, { key: "status", label: "Status" }], rows: [{ company: "Atlas", score: 91, status: "Covered" }, { company: "Nova", score: 84, status: "Review" }, { company: "Vector", score: 78, status: "Watching" }] },
  ]),
  specimen("CodeBlock", "展示代码或机器输出，支持文件名、行号和复制。", [
    { id: "root", component: "CodeBlock", filename: "surface.json", language: "json", showLineNumbers: true, code: "{\n  \"component\": \"Card\",\n  \"children\": [\"body\"]\n}" },
  ]),
  specimen("Callout", "强调需要注意的信息、成功、警告或风险。", [
    { id: "root", component: "Stack", children: ["info", "success", "warning"], gap: "sm" },
    { id: "info", component: "Callout", title: "New evidence", content: "Three relevant documents were added.", tone: "info", icon: "info" },
    { id: "success", component: "Callout", title: "Thesis supported", content: "The latest data confirms the core assumption.", tone: "success", icon: "complete" },
    { id: "warning", component: "Callout", title: "Risk changed", content: "Review the supply-side assumption.", tone: "warning", icon: "alert" },
  ]),
  specimen("Avatar", "展示人物、Agent 或实体的头像与身份说明。", [
    { id: "root", component: "Avatar", name: "Valuz A2UI", description: "Base catalog · 51 components", shape: "rounded", size: "lg" },
  ]),
  specimen("Progress", "表达进度、完成度或有上下界的比例值。", [
    { id: "root", component: "Progress", label: "Research coverage", value: 76, tone: "success" },
  ]),
  specimen("Skeleton", "内容加载期间保持页面结构稳定。", [
    { id: "root", component: "Skeleton", variant: "text", lines: 5 },
  ]),
  specimen("EmptyState", "表达空数据、无结果或尚未开始的状态。", [
    { id: "root", component: "EmptyState", title: "No saved research yet", description: "Start a conversation and ask Valuz to build this surface.", icon: "sparkles" },
  ]),
];

const actionSpecimens = [
  specimen("Button", "触发明确的 A2UI Action，支持图标、尺寸和视觉变体。", [
    { id: "root", component: "Button", label: "Generate surface", icon: "sparkles", action: { event: { name: "gallery.generate" } } },
  ]),
  specimen("ButtonGroup", "排列一组并列或相互关联的操作。", [
    { id: "root", component: "ButtonGroup", children: ["primary", "outline", "ghost"], align: "start" },
    { id: "primary", component: "Button", label: "Save", action: { event: { name: "gallery.save" } } },
    { id: "outline", component: "Button", label: "Preview", variant: "outline", action: { event: { name: "gallery.preview" } } },
    { id: "ghost", component: "Button", label: "Cancel", variant: "ghost", action: { event: { name: "gallery.cancel" } } },
  ]),
  specimen("FollowUpBlock", "由 Agent 给出自然的后续问题或下一步操作。", [
    { id: "root", component: "FollowUpBlock", title: "Suggested next steps", layout: "grid", items: [{ label: "Inspect the evidence", description: "Review the strongest supporting signals", icon: "search", action: { event: { name: "gallery.evidence" } } }, { label: "Create a monitor", description: "Track the key assumption every week", icon: "trend", action: { event: { name: "gallery.monitor" } } }] },
  ]),
];

const formData = {
  query: "AI infrastructure",
  notes: "Focus on durable demand and supply constraints.",
  region: "global",
  horizon: "year",
  coverage: ["companies", "industries"],
  date: "2026-08-12",
  confidence: 70,
  notifications: ["material"],
  density: ["comfortable"],
};
const options = {
  regions: [{ label: "Global", value: "global" }, { label: "United States", value: "us" }, { label: "Asia Pacific", value: "apac" }],
  horizons: [{ label: "Quarter", value: "quarter" }, { label: "Year", value: "year" }, { label: "Long term", value: "long" }],
  coverage: [{ label: "Companies", value: "companies" }, { label: "Industries", value: "industries" }, { label: "Macro", value: "macro" }],
};
const formSpecimens = [
  specimen("Form", "组织多个绑定字段，并把确认动作作为 A2UI Action 提交。", [
    { id: "root", component: "Form", children: ["query", "region"], submitLabel: "Save settings", submit: { event: { name: "gallery.submit", context: { query: { path: "/query" } } } } },
    { id: "query", component: "Input", label: "Research topic", value: { path: "/query" } },
    { id: "region", component: "Select", label: "Region", value: { path: "/region" }, options: options.regions },
  ], formData),
  specimen("Input", "单行文本输入，可读取和写回 A2UI Data Model。", [
    { id: "root", component: "Input", label: "Research topic", description: "Literal or bound string", value: { path: "/query" }, placeholder: "Describe what you want to study" },
  ], formData),
  specimen("TextArea", "输入较长的说明、背景或自由文本。", [
    { id: "root", component: "TextArea", label: "Context", value: { path: "/notes" }, rows: 4 },
  ], formData),
  specimen("Select", "从有限选项中选择一个值。", [
    { id: "root", component: "Select", label: "Region", value: { path: "/region" }, options: options.regions },
  ], formData),
  specimen("RadioGroup", "以互斥选项表达单选决策。", [
    { id: "root", component: "RadioGroup", label: "Time horizon", value: { path: "/horizon" }, orientation: "horizontal", options: options.horizons },
  ], formData),
  specimen("CheckboxGroup", "从一组选项中选择多个值。", [
    { id: "root", component: "CheckboxGroup", label: "Coverage", value: { path: "/coverage" }, orientation: "horizontal", options: options.coverage },
  ], formData),
  specimen("Slider", "在给定范围和步长内调整数值。", [
    { id: "root", component: "Slider", label: "Confidence threshold", value: { path: "/confidence" }, min: 0, max: 100, step: 5, unit: "%" },
  ], formData),
  specimen("DatePicker", "选择日期或日期时间。", [
    { id: "root", component: "DatePicker", label: "Review date", value: { path: "/date" } },
  ], formData),
  specimen("SwitchGroup", "表达多项可以独立启停的设置。", [
    { id: "root", component: "SwitchGroup", label: "Notifications", value: { path: "/notifications" }, options: [{ label: "Material changes", description: "Only high-signal updates", value: "material" }, { label: "Weekly summary", description: "One digest every Friday", value: "weekly" }] },
  ], formData),
  specimen("ToggleGroup", "用紧凑分段按钮选择一个或多个模式。", [
    { id: "root", component: "ToggleGroup", label: "Density", value: { path: "/density" }, options: [{ label: "Compact", value: "compact" }, { label: "Comfortable", value: "comfortable" }, { label: "Spacious", value: "spacious" }] },
  ], formData),
];

const chartSpecimens = [
  specimen("LineChart", "比较一个或多个序列随有序横轴的变化趋势。", [
    { id: "root", component: "LineChart", title: "Revenue and margin trend", data: { path: "/trend" }, xKey: "period", series: [{ key: "revenue", label: "Revenue" }, { key: "margin", label: "Margin" }], height: 240 },
  ], { trend }),
  specimen("AreaChart", "通过填充面积强调规模和累计趋势。", [
    { id: "root", component: "AreaChart", title: "Demand trend", data: { path: "/trend" }, xKey: "period", series: [{ key: "demand", label: "Demand" }], height: 240 },
  ], { trend }),
  specimen("BarChart", "使用纵向柱形比较离散类别。", [
    { id: "root", component: "BarChart", title: "Revenue vs cost", data: { path: "/trend" }, xKey: "period", series: [{ key: "revenue", label: "Revenue" }, { key: "cost", label: "Cost" }], height: 240 },
  ], { trend }),
  specimen("HorizontalBarChart", "适合排名和较长类别标签的横向比较。", [
    { id: "root", component: "HorizontalBarChart", title: "Infrastructure score", data: { path: "/ranking" }, categoryKey: "name", series: [{ key: "score", label: "Score" }], height: 240, showLegend: false },
  ], { ranking }),
  specimen("PieChart", "使用完整扇区表达少量类别的占比。", [
    { id: "root", component: "PieChart", title: "Revenue mix", data: { path: "/mix" }, nameKey: "name", valueKey: "value", height: 240 },
  ], { mix }),
  specimen("DonutChart", "以环形占比和中心标签突出整体结构。", [
    { id: "root", component: "DonutChart", title: "Revenue mix", data: { path: "/mix" }, nameKey: "name", valueKey: "value", centerLabel: "100%", height: 240 },
  ], { mix }),
  specimen("ComboChart", "在同一坐标系叠加 Bar、Line、Area，可使用 stack 和左右轴。", [
    { id: "root", component: "Stack", children: ["bar-line-card", "stacked-card", "area-line-card"], gap: "lg" },
    { id: "bar-line-card", component: "Card", title: "Bar + Line · dual axis", children: ["bar-line"], variant: "muted", padding: "sm" },
    { id: "bar-line", component: "ComboChart", data: { path: "/trend" }, xKey: "period", rightAxis: true, series: [{ key: "revenue", label: "Revenue", type: "bar" }, { key: "margin", label: "Margin %", type: "line", axis: "right" }], height: 220 },
    { id: "stacked-card", component: "Card", title: "Stacked Bar + Line", children: ["stacked"], variant: "muted", padding: "sm" },
    { id: "stacked", component: "ComboChart", data: { path: "/trend" }, xKey: "period", rightAxis: true, series: [{ key: "cost", label: "Cost", type: "bar", stack: "total" }, { key: "margin", label: "Margin", type: "bar", stack: "total" }, { key: "revenue", label: "Revenue", type: "line", axis: "right" }], height: 220 },
    { id: "area-line-card", component: "Card", title: "Area + Line", children: ["area-line"], variant: "muted", padding: "sm" },
    { id: "area-line", component: "ComboChart", data: { path: "/trend" }, xKey: "period", series: [{ key: "demand", label: "Demand", type: "area" }, { key: "capacity", label: "Capacity", type: "line" }], height: 220 },
  ], { trend }),
  specimen("FunnelChart", "展示从上游到下游逐级收窄的过程。", [
    { id: "root", component: "FunnelChart", title: "Research funnel", data: { path: "/stages" }, nameKey: "name", valueKey: "value", height: 240 },
  ], { stages: [{ name: "Ideas", value: 120 }, { name: "Screened", value: 72 }, { name: "Researched", value: 36 }, { name: "Conviction", value: 12 }] }),
  specimen("TreemapChart", "用矩形面积比较类别或层级结构中的规模。", [
    { id: "root", component: "TreemapChart", title: "Exposure map", data: { path: "/mix" }, nameKey: "name", valueKey: "value", height: 240 },
  ], { mix }),
  specimen("SankeyChart", "通过连线宽度展示节点之间的加权流向。", [
    { id: "root", component: "SankeyChart", title: "Capital flow", data: { path: "/flow" }, height: 240 },
  ], { flow: { nodes: [{ name: "Capital" }, { name: "Compute" }, { name: "Network" }, { name: "Growth" }, { name: "Efficiency" }], links: [{ source: 0, target: 1, value: 62 }, { source: 0, target: 2, value: 38 }, { source: 1, target: 3, value: 44 }, { source: 1, target: 4, value: 18 }, { source: 2, target: 4, value: 38 }] } }),
  specimen("HeatmapChart", "以颜色强度比较两个分类维度中的数值。", [
    { id: "root", component: "HeatmapChart", title: "Signal intensity", data: { path: "/heatmap" }, xKey: "period", yKey: "signal", valueKey: "value", height: 230 },
  ], { heatmap: ["Q1", "Q2", "Q3", "Q4"].flatMap((period, x) => ["Demand", "Supply", "Pricing"].map((signal, y) => ({ period, signal, value: 18 + x * 17 + y * 11 }))) }),
  specimen("GaugeChart", "在明确上下界内突出一个关键数值。", [
    { id: "root", component: "GaugeChart", title: "Thesis confidence", value: { path: "/confidence" }, min: 0, max: 100, unit: "%", height: 220 },
  ], { confidence: 78 }),
  specimen("SparklineChart", "用极少界面元素表达紧凑趋势。", [
    { id: "root", component: "SparklineChart", title: "Weekly signal", data: { path: "/trend" }, xKey: "period", series: [{ key: "revenue", label: "Signal" }], height: 100 },
  ], { trend }),
  specimen("RadarChart", "比较多个对象在相同维度上的轮廓。", [
    { id: "root", component: "RadarChart", title: "Research profile", data: { path: "/radar" }, categoryKey: "dimension", series: [{ key: "alpha", label: "Alpha" }, { key: "beta", label: "Beta" }], domainMax: 100, height: 240 },
  ], { radar: [{ dimension: "Growth", alpha: 84, beta: 62 }, { dimension: "Margin", alpha: 72, beta: 80 }, { dimension: "Durability", alpha: 91, beta: 70 }, { dimension: "Valuation", alpha: 55, beta: 76 }, { dimension: "Catalysts", alpha: 79, beta: 68 }] }),
  specimen("RadialChart", "使用同心径向条表达多项有界数值。", [
    { id: "root", component: "RadialChart", title: "Category scores", data: { path: "/mix" }, nameKey: "name", valueKey: "value", height: 240 },
  ], { mix }),
  specimen("ScatterChart", "揭示两个数值维度之间的相关性、聚类和异常点。", [
    { id: "root", component: "ScatterChart", title: "Growth vs margin", data: { path: "/scatter" }, xKey: "growth", yKey: "margin", sizeKey: "scale", seriesName: "Companies", height: 240 },
  ], { scatter: [{ growth: 12, margin: 22, scale: 40 }, { growth: 18, margin: 16, scale: 80 }, { growth: 24, margin: 31, scale: 120 }, { growth: 8, margin: 36, scale: 60 }, { growth: 29, margin: 12, scale: 100 }] }),
];

export const GALLERY_CATEGORIES: GalleryCategory[] = [
  { id: "layout", label: "布局与容器", eyebrow: "LAYOUT", description: "组织页面结构、层级、切换和渐进披露。", specimens: layoutSpecimens },
  { id: "content", label: "内容与数据", eyebrow: "CONTENT", description: "呈现文本、媒体、列表、表格和各种反馈状态。", specimens: contentSpecimens },
  { id: "actions", label: "操作与引导", eyebrow: "ACTIONS", description: "把 Agent 的建议转换为明确、可追踪的用户动作。", specimens: actionSpecimens },
  { id: "forms", label: "表单与输入", eyebrow: "FORMS", description: "通过官方 A2UI Data Model 读取和写回用户输入。", specimens: formSpecimens },
  { id: "charts", label: "图表与可视化", eyebrow: "CHARTS", description: "覆盖比较、趋势、构成、关系、流向和多类型叠加。", specimens: chartSpecimens },
];

export const GALLERY_COMPONENT_NAMES = GALLERY_CATEGORIES.flatMap((category) =>
  category.specimens.map((item) => item.name),
);
