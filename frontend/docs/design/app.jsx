const { useState, useEffect, useRef, useMemo } = React;

/* ============ ICONS ============ */
// Lucide-compatible local SVG registry so icons render correctly without external runtime dependencies.
const SIDEBAR_ICON_STROKE = 2;
const SIDEBAR_SECTION_COLOR = "#6E7481";
const COMPOSER_HOVER_BG = "#F7F8FA";
const DISCLOSURE_CHEVRON_SIZE = 12;
const DISCLOSURE_CHEVRON_STROKE = 2;
const DISCLOSURE_CHEVRON_COLOR = "#94A3B8";
const TEXT_PRIMARY_COLOR = "#131313";
const TEXT_SECONDARY_COLOR = "#6E7481";
const TEXT_DISABLED_COLOR = "#DBDBDB";
const FINANCE_UP_COLOR = "#F54B4B";
const FINANCE_DOWN_COLOR = "#53BC76";
const DESIGN_TOKENS = Object.freeze({
  color: {
    frameBg: "#5A5866",
    appBg: "#F8F9FB",
    surface: "#FFFFFF",
    surfaceMuted: "#F7F8FA",
    surfaceSubtle: "#F5F5F4",
    border: "#E6E7E9",
    divider: "#F3F4F6",
    textPrimary: TEXT_PRIMARY_COLOR,
    textSecondary: TEXT_SECONDARY_COLOR,
    textDisabled: TEXT_DISABLED_COLOR,
    textTertiary: "#A8A29E",
    chevron: DISCLOSURE_CHEVRON_COLOR,
    accentSky: "#0EA5E9",
    accentTeal: "#14B8A6",
    accentAmber: "#EAB308",
    accentPink: "#EC4899",
    contextIcon: "#725CF9",
    windowRed: "#FF5F57",
    windowYellow: "#FEBC2E",
    windowGreen: "#28C840",
  },
  type: {
    badge: 9.5,
    xs: 10,
    sm: 10.5,
    ui: 11,
    uiPlus: 11.5,
    bodySm: 12,
    body: 12.5,
    label: 13,
    message: 13.5,
    title: 14,
    panelTitle: 15,
  },
  radius: {
    xs: 3,
    sm: 4,
    md: 6,
    row: 7,
    card: 8,
    panel: 10,
    shell: 12,
    page: 14,
  },
  spacing: {
    micro: 4,
    tight: 6,
    base: 8,
    row: 9,
    content: 10,
    section: 12,
    panelX: 20,
    messageX: 24,
    messageY: 28,
  },
  shadow: {
    app: "0 50px 100px -20px rgba(0, 0, 0, 0.45), 0 30px 60px -30px rgba(0, 0, 0, 0.35), 0 0 0 1px rgba(0,0,0,0.06)",
    floating: "0 24px 48px rgba(28, 25, 23, 0.14)",
    dropdown: "0 18px 40px -18px rgba(17,24,39,0.28), 0 8px 16px -12px rgba(17,24,39,0.18)",
    popover: "0 12px 32px -8px rgba(0,0,0,0.12), 0 2px 6px rgba(0,0,0,0.04)",
    activeRow: "0 4px 10px rgba(217, 221, 224, 0.8)",
  },
  layout: {
    chromeTop: 4,
    chromeHeight: 28,
    sidebarWidth: 220,
    contextWidth: 345,
    messageMaxWidth: 760,
  },
  icon: {
    xs: 12,
    sm: 13,
    md: 14,
    lg: 15,
    xl: 16,
    checkbox: 18,
    sidebarStroke: SIDEBAR_ICON_STROKE,
    disclosureSize: DISCLOSURE_CHEVRON_SIZE,
    disclosureStroke: DISCLOSURE_CHEVRON_STROKE,
  },
});
const SHARED_STYLES = Object.freeze({
  panelShell: {
    background: "var(--surface)",
    border: `1px solid ${DESIGN_TOKENS.color.border}`,
    borderRadius: DESIGN_TOKENS.radius.shell,
    overflow: "hidden",
  },
  floatingShell: {
    background: "rgba(248,249,251,0.98)",
    border: `1px solid ${DESIGN_TOKENS.color.border}`,
    borderRadius: DESIGN_TOKENS.radius.shell,
    boxShadow: DESIGN_TOKENS.shadow.floating,
    backdropFilter: "blur(10px)",
    overflow: "hidden",
  },
  menuShell: {
    background: DESIGN_TOKENS.color.surface,
    border: `1px solid ${DESIGN_TOKENS.color.border}`,
    borderRadius: DESIGN_TOKENS.radius.shell,
    boxShadow: DESIGN_TOKENS.shadow.dropdown,
    overflow: "hidden",
  },
  popoverShell: {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: DESIGN_TOKENS.radius.panel,
    boxShadow: DESIGN_TOKENS.shadow.popover,
    overflow: "hidden",
  },
});
const panelShellStyle = (overrides = {}) => ({ ...SHARED_STYLES.panelShell, ...overrides });
const floatingShellStyle = (overrides = {}) => ({ ...SHARED_STYLES.floatingShell, ...overrides });
const menuShellStyle = (overrides = {}) => ({ ...SHARED_STYLES.menuShell, ...overrides });
const popoverShellStyle = (overrides = {}) => ({ ...SHARED_STYLES.popoverShell, ...overrides });
const composerMenuItemStyle = (active = false, overrides = {}) => ({
  width: "100%",
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "10px 12px",
  borderRadius: 10,
  textAlign: "left",
  background: active ? DESIGN_TOKENS.color.surfaceMuted : "transparent",
  color: TEXT_PRIMARY_COLOR,
  transition: "background .12s",
  ...overrides,
});
const composerMenuTitleStyle = (overrides = {}) => ({
  display: "block",
  fontSize: DESIGN_TOKENS.type.body,
  color: TEXT_PRIMARY_COLOR,
  ...overrides,
});
const composerMenuDescriptionStyle = (overrides = {}) => ({
  display: "block",
  fontSize: DESIGN_TOKENS.type.uiPlus,
  color: TEXT_SECONDARY_COLOR,
  marginTop: 1,
  ...overrides,
});
const composerMenuEyebrowStyle = (overrides = {}) => ({
  padding: "10px 12px 4px",
  fontSize: DESIGN_TOKENS.type.sm,
  fontWeight: 600,
  letterSpacing: "0.08em",
  color: TEXT_SECONDARY_COLOR,
  ...overrides,
});
const composerMenuSectionStyle = (overrides = {}) => ({
  padding: 6,
  ...overrides,
});
const sectionLabelStyle = (overrides = {}) => ({
  fontSize: DESIGN_TOKENS.type.uiPlus,
  fontWeight: 400,
  color: SIDEBAR_SECTION_COLOR,
  letterSpacing: "0.06em",
  ...overrides,
});
const sidebarRowStyle = (active, overrides = {}) => ({
  position: "relative",
  display: "flex",
  alignItems: "center",
  gap: DESIGN_TOKENS.spacing.row,
  padding: "7px 10px",
  margin: "0",
  width: "100%",
  borderRadius: DESIGN_TOKENS.radius.row,
  textAlign: "left",
  color: DESIGN_TOKENS.color.textPrimary,
  background: active ? "var(--surface)" : "transparent",
  border: "1px solid transparent",
  boxShadow: active ? DESIGN_TOKENS.shadow.activeRow : "none",
  zIndex: active ? 20 : 1,
  fontSize: DESIGN_TOKENS.type.label,
  fontWeight: 400,
  transition: "background .12s, box-shadow .12s",
  ...overrides,
});
const panelHeaderStyle = (overrides = {}) => ({
  height: 48,
  padding: `0 ${DESIGN_TOKENS.spacing.panelX}px`,
  display: "flex",
  alignItems: "center",
  borderBottom: `1px solid ${DESIGN_TOKENS.color.divider}`,
  background: "var(--surface)",
  ...overrides,
});
const normalizeIconName = (name = "") => String(name).split("-").map((part, index) => (
  index === 0 ? part : part.charAt(0).toUpperCase() + part.slice(1)
)).join("");

const Icon = ({ name, size = 16, stroke = 1.6, color, style, ...rest }) => {
  const props = {
    width: size, height: size, viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor",
    strokeWidth: stroke, strokeLinecap: "round", strokeLinejoin: "round",
    style: { ...(color ? { color } : {}), ...(style || {}) },
    ...rest,
  };
  const paths = {
    plus: <><path d="M12 5v14M5 12h14" /></>,
    send: <><path d="M22 2 11 13" /><path d="m22 2-7 20-4-9-9-4 20-7z" /></>,
    chevDown: <path d="m6 9 6 6 6-6" />,
    chevRight: <path d="m9 6 6 6-6 6" />,
    chevLeft: <path d="m15 6-6 6 6 6" />,
    messageCirclePlus: <><path d="M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719" /><path d="M8 12h8" /><path d="M12 8v8" /></>,
    folder: <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>,
    book: <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20" />,
    sparkles: <><path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z" /><path d="M20 2v4" /><path d="M22 4h-4" /><circle cx="4" cy="20" r="2" /></>,
    clock3: <><circle cx="12" cy="12" r="10" /><path d="M12 6v6h4" /></>,
    settings: <><path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" /><circle cx="12" cy="12" r="3" /></>,
    paperclip: <path d="m21 10-9.3 9.3a5 5 0 0 1-7-7L14 3a3.3 3.3 0 1 1 4.7 4.7L9.4 17a1.7 1.7 0 0 1-2.4-2.4l8.6-8.6"/>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></>,
    fileText: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h6"/></>,
    check: <path d="m5 12 5 5L20 7"/>,
    square: <rect x="3" y="3" width="18" height="18" rx="3"/>,
    squareCheck: <><rect x="3" y="3" width="18" height="18" rx="3"/><path d="m8 12 3 3 5-6"/></>,
    checkbox: <rect x="4" y="4" width="16" height="16" rx="3" />,
    checkboxFilled: <><rect x="4" y="4" width="16" height="16" rx="3" fill="currentColor" stroke="currentColor" /><path d="m8 12 3 3 5-6" stroke="#fff" /></>,
    terminal: <><path d="m4 17 6-6-6-6"/><path d="M12 19h8"/></>,
    zap: <path d="M13 2 3 14h9l-1 8 10-12h-9z"/>,
    layers: <><path d="m12 2 10 6-10 6L2 8z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/></>,
    panelRight: <><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/></>,
    panelLeft: <><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/></>,
    dot: <circle cx="12" cy="12" r="3" fill="currentColor"/>,
    at: <><circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/></>,
    slash: <path d="M5 20 19 4"/>,
    arrow: <><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></>,
    heroArrowUp: <path d="M4.5 10.5 12 3m0 0 7.5 7.5M12 3v18" />,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>,
    copy: <><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></>,
    refresh: <><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></>,
    thumb: <path d="M7 10v12h10.3a2 2 0 0 0 2-1.7L21 11a1 1 0 0 0-1-1.2h-5.3L16 4a2 2 0 0 0-2-2 1 1 0 0 0-.9.6L7 10z"/>,
    x: <path d="M18 6 6 18M6 6l12 12"/>,
    sidebar: <><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/></>,
    lightning: <path d="M13 2 4.1 13.4a.5.5 0 0 0 .4.8H11l-1 7.3a.5.5 0 0 0 .9.4L20 10.2a.5.5 0 0 0-.4-.8H13l1-7.4z"/>,
    chart: <><path d="M3 3v18h18"/><path d="m7 14 4-4 4 4 5-6"/></>,
    database: <><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.7-4 3-9 3s-9-1.3-9-3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/></>,
    globe: <><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/></>,
    play: <polygon points="6 4 20 12 6 20 6 4" fill="currentColor" stroke="none"/>,
    pause: <><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/></>,
    bolt: <path d="M13 2 4.1 13.4a.5.5 0 0 0 .4.8H11l-1 7.3a.5.5 0 0 0 .9.4L20 10.2a.5.5 0 0 0-.4-.8H13l1-7.4z"/>,
    flask: <><path d="M9 3h6"/><path d="M10 3v7L4.5 20a1.5 1.5 0 0 0 1.4 2h12.2a1.5 1.5 0 0 0 1.4-2L14 10V3"/></>,
  };
  const icon = paths[normalizeIconName(name)] || paths[name];
  return <svg {...props}>{icon}</svg>;
};

/* ============ ACCENT THEMES ============ */
const ACCENTS = {
  indigo:  { accent: "#6d5cff", accent2: "#8b7fff", soft: "#ede9ff" },
  emerald: { accent: "#059669", accent2: "#10b981", soft: "#d1fae5" },
  slate:   { accent: "#334155", accent2: "#475569", soft: "#e2e8f0" },
  amber:   { accent: "#b45309", accent2: "#d97706", soft: "#fef3c7" },
  crimson: { accent: "#be123c", accent2: "#e11d48", soft: "#ffe4e6" },
};

/* ============ DATA ============ */
const INITIAL_PROJECTS = [
  { id: "p1", name: "英伟达 2025 深度研究", count: 12 },
  { id: "p2", name: "新能源行业周报", count: 8 },
  { id: "p3", name: "消费电子竞争格局", count: 5 },
];

const INITIAL_RECENTS = [
  { id: "c1", title: "英伟达 Q4 财报分析", time: "10:30", active: true, project: "英伟达 2025 深度研究" },
  { id: "c2", title: "今日市场概况", time: "09:14", project: null },
  { id: "c3", title: "美联储会议利率决议", time: "昨天", project: null },
  { id: "c4", title: "行业对比：AMD vs NVDA", time: "昨天", project: "英伟达 2025 深度研究" },
  { id: "c5", title: "光伏产业链梳理", time: "周一", project: "新能源行业周报" },
  { id: "c6", title: "台积电产能爬坡", time: "4 月 18 日", project: null },
  { id: "c7", title: "AI 服务器供应链更新", time: "11:42", project: "英伟达 2025 深度研究" },
  { id: "c8", title: "HBM 价格趋势跟踪", time: "11:06", project: null },
  { id: "c9", title: "云厂商 CapEx 对比", time: "10:48", project: null },
  { id: "c10", title: "Blackwell 出货节奏", time: "10:02", project: "英伟达 2025 深度研究" },
  { id: "c11", title: "台股 AI 代工映射", time: "08:51", project: null },
  { id: "c12", title: "存储厂商涨价影响", time: "08:18", project: null },
  { id: "c13", title: "北美云服务商电话会摘录", time: "昨天", project: null },
  { id: "c14", title: "AMD MI325 芯片对比", time: "昨天", project: null },
  { id: "c15", title: "英伟达渠道库存变化", time: "昨天", project: "英伟达 2025 深度研究" },
  { id: "c16", title: "国产算力链景气度", time: "周二", project: null },
  { id: "c17", title: "服务器整机厂利润率复盘", time: "周三", project: null },
  { id: "c18", title: "交换芯片竞争格局", time: "周四", project: null },
  { id: "c19", title: "液冷基础设施受益标的", time: "周五", project: null },
  { id: "c20", title: "数据中心电力约束", time: "4 月 16 日", project: null },
  { id: "c21", title: "GB200 机柜 BOM 拆解", time: "4 月 12 日", project: "英伟达 2025 深度研究" },
];

const INITIAL_TODOS = [
  { id: "t1", text: "对比 Q3/Q4 数据中心毛利率", done: true },
  { id: "t2", text: "整理营收同比数据", done: true },
  { id: "t3", text: "计算数据中心环比增速", done: false },
  { id: "t4", text: "检索近 30 日分析师评级", done: false },
  { id: "t5", text: "起草研报初稿", done: false },
];

const GENERATED_FILES = [
  { id: "f1", name: "英伟达 Q4 分析.md", size: "12.4 KB", kind: "md" },
  { id: "f2", name: "数据汇总.csv", size: "3.1 KB", kind: "csv" },
  { id: "f3", name: "毛利率对比.png", size: "88 KB", kind: "img" },
  { id: "f4", name: "研报大纲.md", size: "6.2 KB", kind: "md" },
  { id: "f5", name: "同比增速.xlsx", size: "22 KB", kind: "xlsx" },
];

const UPLOAD_FILES = [
  { id: "u1", name: "Q4 财报.pdf", size: "2.1 MB" },
];

const DEFAULT_PROJECT_NAME = INITIAL_PROJECTS[0].name;
const EMPTY_STATE_CHAT_TITLE = "新建对话";
const EMPTY_STATE_SUGGESTIONS = [
  "拆解英伟达最新财报",
  "判断 Blackwell 出货节奏",
  "对比 NVDA 与 AMD 估值",
];

/* ============ STARTING CONVERSATION ============ */
const INITIAL_MESSAGES = [
  {
    id: "m1", role: "user", time: "10:28",
    text: "帮我分析一下英伟达最新季度财报，重点看数据中心业务和毛利率变化",
  },
  {
    id: "m2", role: "agent", time: "10:28",
    text: "好。我先从知识库中检索最新的英伟达 Q4 财报原文，再调用研报撰写模板整理关键要点。",
    tools: [
      { id: "t1", name: "kb_search", label: "检索「英伟达 Q4 数据中心」", status: "done", ms: 820, kind: "kb",
        detail: "命中 3 份文档：Q4 财报原文、CFO 电话会议纪要、投资者简报。" },
      { id: "t2", name: "fetch_stock_price", label: "获取 NVDA 最新行情", status: "done", ms: 310, kind: "web",
        detail: "$912.34 (+2.14%)  市值 $2.24T  VIX 16.8" },
      { id: "t3", name: "skill: 研报撰写模板", label: "按模板整理财报要点", status: "running", ms: null, kind: "skill",
        detail: "正在生成：一、业绩概览 / 二、分业务拆解 / 三、毛利率分析 / 四、展望与风险…" },
    ],
  },
  {
    id: "m3", role: "agent", time: "10:30",
    text: "**一、业绩概览**\n英伟达 FY25Q4 实现营收 **$39.3B**（同比 +78%，环比 +12%），净利润 **$22.1B**（同比 +80%），再创历史新高。数据中心业务贡献核心增量。\n\n**二、分业务拆解**\n- **数据中心**：$35.6B（同比 +93%，占总营收 90.6%），Hopper 持续放量，Blackwell 进入规模交付。\n- **游戏**：$2.5B（同比 −11%），RTX 50 系列换代扰动。\n- **专业可视化 & 汽车**：$0.6B / $0.57B，合计贡献边际。\n\n**三、毛利率分析**\n非 GAAP 毛利率 **73.5%**，较 Q3 的 75.0% 回落 150bp。主要受 Blackwell 初期良率与封装成本上行影响，管理层指引 Q1 毛利率 **71%±50bp**。",
    data: {
      kind: "revenue-table",
      rows: [
        { seg: "数据中心", q4: 35.6, q3: 30.8, yoy: "+93%", qoq: "+15.6%" },
        { seg: "游戏", q4: 2.5, q3: 3.3, yoy: "−11%", qoq: "−24%" },
        { seg: "专业可视化", q4: 0.60, q3: 0.49, yoy: "+10%", qoq: "+22%" },
        { seg: "汽车", q4: 0.57, q3: 0.45, yoy: "+103%", qoq: "+28%" },
      ],
    },
  },
];

function buildSeedMessages(chat) {
  if (!chat || chat.id === "c1") return INITIAL_MESSAGES;

  const title = chat.title;
  const projectText = chat.project ? `，并结合项目「${chat.project}」里的上下文` : "";

  return [
    {
      id: `${chat.id}-m1`,
      role: "user",
      time: "10:28",
      text: `帮我整理一下「${title}」的核心信息，重点看结论、驱动因素和风险点。`,
    },
    {
      id: `${chat.id}-m2`,
      role: "agent",
      time: "10:28",
      text: `好。我先检索与「${title}」相关的资料${projectText}，再按结构整理关键要点。`,
      tools: [
        {
          id: `${chat.id}-t1`,
          name: "kb_search",
          label: `检索「${title}」相关资料`,
          status: "done",
          ms: 760,
          kind: "kb",
          detail: "命中 3 份文档：原始资料、会议纪要、摘要整理。",
        },
        {
          id: `${chat.id}-t2`,
          name: "fetch_stock_price",
          label: "补充相关市场数据",
          status: "done",
          ms: 280,
          kind: "web",
          detail: "已抓取最近一个交易日价格变化、成交量与板块对比。",
        },
        {
          id: `${chat.id}-t3`,
          name: "skill: 研报撰写模板",
          label: "按模板整理分析结论",
          status: "running",
          ms: null,
          kind: "skill",
          detail: "正在生成：一、核心结论 / 二、驱动因素 / 三、风险提示 / 四、后续跟踪点…",
        },
      ],
    },
  ];
}

function buildChatTitleFromPrompt(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return EMPTY_STATE_CHAT_TITLE;
  return normalized.length > 18 ? `${normalized.slice(0, 18)}…` : normalized;
}

function buildFirstTurnReply(text, chat) {
  const title = buildChatTitleFromPrompt(text).replace(/…$/, "");
  const projectText = chat?.project ? `，并结合项目「${chat.project}」已有资料` : "";
  const timestamp = Date.now();
  return {
    id: `a${timestamp}`,
    role: "agent",
    time: nowTime(),
    text: `好。我先围绕「${title}」梳理关键事实${projectText}，再给你一版结论、驱动因素和风险点的结构化摘要。`,
    tools: [
      {
        id: `ft-${timestamp}-1`,
        name: "kb_search",
        label: `检索「${title}」相关资料`,
        status: "done",
        ms: 680,
        kind: "kb",
        detail: "已关联项目内知识库、近期纪要与历史分析记录。",
      },
      {
        id: `ft-${timestamp}-2`,
        name: "web_research",
        label: "补充最新市场与公司动态",
        status: "done",
        ms: 420,
        kind: "web",
        detail: "正在汇总最新财报、行情表现与产业链变化。",
      },
      {
        id: `ft-${timestamp}-3`,
        name: "skill: 深度研究摘要",
        label: "生成首轮结构化分析",
        status: "running",
        ms: null,
        kind: "skill",
        detail: "准备输出：核心结论 / 关键驱动 / 风险提示 / 后续跟踪问题。",
      },
    ],
  };
}

const MARKET_OVERVIEW_MESSAGES = [
  {
    id: "c2-m1",
    role: "user",
    time: "09:14",
    headerLabel: "User",
    attachmentPlacement: "above",
    text: "帮我分析这份晨会摘要，并结合今天 A 股指数、北向资金和板块轮动，给我一个简明市场结论。",
    attachments: [
      { id: "c2-a1", kind: "pdf", name: "开盘晨会摘要.pdf", size: "2.3 MB", status: "已解析 ok" },
    ],
  },
  {
    id: "c2-m2",
    role: "agent",
    time: "09:14",
    headerLabel: "Agent",
    showHeader: true,
    tools: [
      {
        id: "c2-t1",
        kind: "web",
        title: "实时行情",
        name: "get_market_quote",
        label: "获取上证指数、深证成指、创业板指数据",
        status: "done",
        ms: 820,
        sections: [
          { label: "调用", value: "get_market_quote" },
          { label: "参数", code: '{ "indices": ["000001.SH", "399001.SZ", "399006.SZ"] }' },
          { label: "返回", code: '{ "000001.SH": { "name": "上证指数", "price": 3182.35, "change": 0.42 }, "399001.SZ": { "name": "深证成指", "price": 10241.80, "change": 0.36 }, "399006.SZ": { "name": "创业板指", "price": 1988.44, "change": 0.58 } }' },
        ],
      },
      {
        id: "c2-t2",
        kind: "data",
        title: "北向资金",
        name: "get_northbound_flow",
        label: "拉取沪股通、深股通实时净流入",
        status: "error",
        ms: 410,
        defaultOpen: true,
        sections: [
          { label: "调用", value: "get_northbound_flow" },
          { label: "参数", code: '{ "market": ["SH", "SZ"], "window": "intraday" }' },
          { label: "错误", code: 'Wind session timeout: northbound_flow stream unavailable (retry in 30s).' },
        ],
      },
    ],
  },
  {
    id: "c2-m3",
    role: "agent",
    time: "09:15",
    headerLabel: "Agent",
    showHeader: true,
    tools: [
      {
        id: "c2-s1",
        kind: "skill",
        title: "研报撰写模板",
        name: "skill: 研报撰写模板",
        label: "正在按照标准模板结构生成市场快评",
        status: "running",
        sections: [
          { label: "步骤", value: "1. 汇总指数表现\n2. 归纳资金风格\n3. 提炼板块轮动\n4. 输出盘中结论" },
          { label: "中间输出", code: "已完成指数与成交额概览，正在整理资金风格与强势板块。" },
        ],
      },
    ],
  },
  {
    id: "c2-m4",
    role: "agent",
    time: "09:15",
    headerLabel: "Agent",
    showHeader: true,
    streaming: true,
    text: "根据今日行情数据，我来分析 A 股表现：\n\n**市场结论**\n- 三大指数维持窄幅震荡，盘面风险偏好没有明显扩张。\n- 资金仍偏向高股息与算力链，追涨情绪相对克制。\n- 若午后北向资金回流放大，指数有机会从震荡转向温和修复。",
    showStop: true,
  },
];

const INITIAL_MESSAGES_BY_CHAT = INITIAL_RECENTS.reduce((acc, chat) => {
  acc[chat.id] = chat.id === "c2" ? MARKET_OVERVIEW_MESSAGES : buildSeedMessages(chat);
  return acc;
}, {});

/* ============ HELPERS ============ */
const cls = (...a) => a.filter(Boolean).join(" ");

const useLocal = (key, initial) => {
  const [v, setV] = useState(() => {
    try { const s = localStorage.getItem(key); return s ? JSON.parse(s) : initial; } catch { return initial; }
  });
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)); } catch {} }, [key, v]);
  return [v, setV];
};

/* ============ APP ============ */
function App() {
  // tweaks
  const defaults = JSON.parse(document.getElementById("tweak-defaults").textContent.replace(/\/\*EDITMODE-BEGIN\*\/|\/\*EDITMODE-END\*\//g, ""));
  const [tweaks, setTweaks] = useLocal("valuz.tweaks", defaults);
  const [editMode, setEditMode] = useState(false);

  useEffect(() => {
    const handler = (e) => {
      if (e.data?.type === "__activate_edit_mode") setEditMode(true);
      if (e.data?.type === "__deactivate_edit_mode") setEditMode(false);
    };
    window.addEventListener("message", handler);
    window.parent.postMessage({ type: "__edit_mode_available" }, "*");
    return () => window.removeEventListener("message", handler);
  }, []);

  const updateTweak = (k, v) => {
    setTweaks((t) => ({ ...t, [k]: v }));
    window.parent.postMessage({ type: "__edit_mode_set_keys", edits: { [k]: v } }, "*");
  };

  // app state
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [contextOpen, setContextOpen] = useState(tweaks.contextOpen !== false);
  const [activeChatId, setActiveChatId] = useState("c1");
  const [recents, setRecents] = useState(INITIAL_RECENTS);
  const [messagesByChat, setMessagesByChat] = useState(INITIAL_MESSAGES_BY_CHAT);
  const [todos, setTodos] = useState(INITIAL_TODOS);
  const [todosOpen, setTodosOpen] = useState(true);
  const [filesOpen, setFilesOpen] = useState(false);
  const [uploadsOpen, setUploadsOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [input, setInput] = useState("");
  const [slashOpen, setSlashOpen] = useState(false);
  const [atOpen, setAtOpen] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [sidebarPeekOpen, setSidebarPeekOpen] = useState(false);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const sidebarPeekTimerRef = useRef(null);
  const replyTimerRef = useRef(null);

  const accent = ACCENTS[tweaks.accent] || ACCENTS.indigo;
  const isDark = tweaks.mode === "dark";
  const activeChat = recents.find((chat) => chat.id === activeChatId) || null;
  const messages = messagesByChat[activeChatId] || buildSeedMessages(activeChat);
  const isEmptyState = Array.isArray(messages) && messages.length === 0;
  const hasAgentMessage = Array.isArray(messages) && messages.some((msg) => msg.role === "agent");
  const hasStreamingAgentMessage = Array.isArray(messages) && messages.some((msg) => msg.role === "agent" && (msg.streaming || msg.showStop));
  const showContextToggle = !isEmptyState && hasAgentMessage;
  const showContextPanel = contextOpen && showContextToggle;
  const appColumns = [
    sidebarOpen ? "var(--sidebar-w)" : null,
    "1fr",
    showContextPanel ? "var(--context-w)" : null,
  ].filter(Boolean).join(" ");
  const appGap = sidebarOpen || showContextPanel ? 8 : 0;
  const appPadding = `36px 16px 16px ${sidebarOpen ? "0px" : "16px"}`;

  // apply accent + mode
  useEffect(() => {
    const r = document.documentElement.style;
    r.setProperty("--accent", accent.accent);
    r.setProperty("--accent-2", accent.accent2);
    r.setProperty("--accent-soft", accent.soft);
    if (isDark) {
      r.setProperty("--bg", "#0f0e13");
      r.setProperty("--surface", "#17151c");
      r.setProperty("--surface-2", "#1f1c26");
      r.setProperty("--border", "#2a2632");
      r.setProperty("--border-strong", "#3a3544");
      r.setProperty("--text", "#f4f4f5");
      r.setProperty("--text-2", "#a1a1aa");
      r.setProperty("--text-3", "#71717a");
    } else {
      r.setProperty("--bg", "#fafaf9");
      r.setProperty("--surface", "#ffffff");
      r.setProperty("--surface-2", "#f5f5f4");
      r.setProperty("--border", "#e7e5e4");
      r.setProperty("--border-strong", "#d6d3d1");
      r.setProperty("--text", "#1c1917");
      r.setProperty("--text-2", "#57534e");
      r.setProperty("--text-3", "#a8a29e");
    }
  }, [accent, isDark]);

  useEffect(() => {
    messagesEndRef.current?.scrollTo({ top: 1e9, behavior: "smooth" });
  }, [messages, thinking]);

  useEffect(() => {
    if (sidebarOpen) {
      if (sidebarPeekTimerRef.current) {
        clearTimeout(sidebarPeekTimerRef.current);
        sidebarPeekTimerRef.current = null;
      }
      setSidebarPeekOpen(false);
    }
    return () => {
      if (sidebarPeekTimerRef.current) clearTimeout(sidebarPeekTimerRef.current);
    };
  }, [sidebarOpen]);

  useEffect(() => {
    return () => {
      if (replyTimerRef.current) clearTimeout(replyTimerRef.current);
    };
  }, []);

  const openSidebarPeek = () => {
    if (sidebarOpen) return;
    if (sidebarPeekTimerRef.current) {
      clearTimeout(sidebarPeekTimerRef.current);
      sidebarPeekTimerRef.current = null;
    }
    setSidebarPeekOpen(true);
  };

  const scheduleSidebarPeekClose = () => {
    if (sidebarOpen) return;
    if (sidebarPeekTimerRef.current) clearTimeout(sidebarPeekTimerRef.current);
    sidebarPeekTimerRef.current = setTimeout(() => {
      setSidebarPeekOpen(false);
      sidebarPeekTimerRef.current = null;
    }, 140);
  };

  const toggleTodo = (id) => setTodos((ts) => ts.map((t) => t.id === id ? { ...t, done: !t.done } : t));
  const doneCount = todos.filter(t => t.done).length;

  const stopGeneration = () => {
    if (replyTimerRef.current) {
      clearTimeout(replyTimerRef.current);
      replyTimerRef.current = null;
    }
    setThinking(false);
    setMessagesByChat((prev) => {
      const baseMessages = prev[activeChatId] || buildSeedMessages(activeChat) || [];
      let changed = false;
      const nextMessages = baseMessages.map((msg) => {
        if (msg.role === "agent" && (msg.streaming || msg.showStop)) {
          changed = true;
          return { ...msg, streaming: false, showStop: false };
        }
        return msg;
      });
      return changed ? { ...prev, [activeChatId]: nextMessages } : prev;
    });
  };

  const send = (overrideText) => {
    const rawText = typeof overrideText === "string" ? overrideText : input;
    if (!rawText.trim()) return;
    const text = rawText.trim();
    const firstMessage = isEmptyState;
    const chatSnapshot = activeChat;
    const userMsg = { id: "u" + Date.now(), role: "user", time: nowTime(), text };
    if (firstMessage) {
      setRecents((prev) => prev.map((chat) => (
        chat.id === activeChatId
          ? { ...chat, title: buildChatTitleFromPrompt(text), time: "刚刚" }
          : chat
      )));
    }
    setMessagesByChat((prev) => {
      const baseMessages = prev[activeChatId] || buildSeedMessages(chatSnapshot) || [];
      return {
        ...prev,
        [activeChatId]: [...baseMessages, userMsg],
      };
    });
    setInput("");
    setSlashOpen(false);
    setAtOpen(false);
    setThinking(true);
    if (replyTimerRef.current) clearTimeout(replyTimerRef.current);
    // Simulate agent reply
    replyTimerRef.current = setTimeout(() => {
      const slash = text.startsWith("/");
      const reply = firstMessage
        ? buildFirstTurnReply(text, chatSnapshot)
        : slash
          ? {
            id: "a" + Date.now(), role: "agent", time: nowTime(),
            text: `已调用 ${text.split(" ")[0]} 能力。正在按模板结构生成内容…`,
            tools: [
              { id: "sk1", name: `skill: ${text.split(" ")[0].slice(1)}`, label: "按模板结构生成", status: "running", kind: "skill", detail: "正在组织章节与数据…" },
            ],
          }
          : {
            id: "a" + Date.now(), role: "agent", time: nowTime(),
            text: "收到。我会基于会话上下文中的 " + doneCount + " 项已完成任务和已上传的 Q4 财报继续推进。你是希望我补充环比增速计算，还是先起草研报初稿？",
          };
      setMessagesByChat((prev) => {
        const baseMessages = prev[activeChatId] || buildSeedMessages(chatSnapshot) || [];
        return {
          ...prev,
          [activeChatId]: [...baseMessages, reply],
        };
      });
      replyTimerRef.current = null;
      setThinking(false);
    }, 1400);
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey) {
      e.preventDefault();
      send();
    } else if (e.key === "Enter" && e.metaKey) {
      e.preventDefault();
      send();
    }
  };

  const onInputChange = (v) => {
    setInput(v);
    setSlashOpen(v === "/" || (v.startsWith("/") && !v.includes(" ")));
    setAtOpen(v.endsWith("@"));
  };

  return (
    <div style={{
      height: "100%",
      width: "100%",
      background: DESIGN_TOKENS.color.frameBg,
      display: "grid",
      placeItems: "center",
      padding: "80px 100px",
      overflow: "auto",
    }}>
      <div style={{
        width: 1440,
        height: 900,
        maxWidth: "100%",
        maxHeight: "100%",
        background: DESIGN_TOKENS.color.appBg,
        borderRadius: DESIGN_TOKENS.radius.shell,
        boxShadow: DESIGN_TOKENS.shadow.app,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}>
        {/* Window chrome */}
        <div style={{
          position: "absolute",
          top: DESIGN_TOKENS.layout.chromeTop,
          left: 18,
          right: 20,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          height: DESIGN_TOKENS.layout.chromeHeight,
          zIndex: 5,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ width: 12, height: 12, borderRadius: "50%", background: DESIGN_TOKENS.color.windowRed, display: "inline-block" }} />
              <span style={{ width: 12, height: 12, borderRadius: "50%", background: DESIGN_TOKENS.color.windowYellow, display: "inline-block" }} />
              <span style={{ width: 12, height: 12, borderRadius: "50%", background: DESIGN_TOKENS.color.windowGreen, display: "inline-block" }} />
            </div>
            <div onMouseEnter={openSidebarPeek} onMouseLeave={scheduleSidebarPeekClose}>
              <TooltipWrap tooltip="切换边栏" side="bottom" offset={4}>
                <button
                  onClick={() => setSidebarOpen((value) => !value)}
                  style={iconBtn()}
                >
                  <Icon name={sidebarOpen ? "panelLeft" : "panelRight"} size={15} stroke={SIDEBAR_ICON_STROKE} color="#6E7481" />
                </button>
              </TooltipWrap>
            </div>
          </div>
          {showContextToggle && (
            <button
              onClick={() => setContextOpen(!contextOpen)}
              style={iconBtn()}
              title="会话上下文"
            >
              <Icon name="panelRight" size={15} stroke={SIDEBAR_ICON_STROKE} color="#6E7481" />
            </button>
          )}
        </div>

    <div className="app" style={{
      display: "grid",
      gridTemplateColumns: appColumns,
      gap: appGap,
      padding: appPadding,
      flex: 1,
      minHeight: 0,
      overflow: "hidden",
      background: "#F8F9FB radial-gradient(ellipse 62% 74% at calc(25% + 50px) calc(35% + 300px), #E0E1EA 0%, transparent 72%) no-repeat",
      transition: "grid-template-columns .25s ease",
    }}>
      {sidebarOpen && (
        <Sidebar
          open={sidebarOpen}
          setOpen={setSidebarOpen}
          recents={recents}
          setRecents={setRecents}
          activeChatId={activeChatId}
          setActiveChatId={setActiveChatId}
          setMessagesByChat={setMessagesByChat}
        />
      )}
      <Main
        activeChat={activeChat}
        isEmptyState={isEmptyState}
        messages={messages}
        thinking={thinking}
        showStopControl={!isEmptyState && (thinking || hasStreamingAgentMessage)}
        input={input}
        onInputChange={onInputChange}
        onKey={onKey}
        send={send}
        onStopGeneration={stopGeneration}
        slashOpen={slashOpen}
        atOpen={atOpen}
        setSlashOpen={setSlashOpen}
        setAtOpen={setAtOpen}
        setInput={setInput}
        contextOpen={contextOpen}
        setContextOpen={setContextOpen}
        messagesEndRef={messagesEndRef}
        inputRef={inputRef}
        showToolTimings={tweaks.showToolTimings}
        emptyStateSuggestions={EMPTY_STATE_SUGGESTIONS}
      />
      <ContextPanel
        open={showContextPanel}
        todos={todos}
        toggleTodo={toggleTodo}
        doneCount={doneCount}
        todosOpen={todosOpen} setTodosOpen={setTodosOpen}
        filesOpen={filesOpen} setFilesOpen={setFilesOpen}
        uploadsOpen={uploadsOpen} setUploadsOpen={setUploadsOpen}
        skillsOpen={skillsOpen} setSkillsOpen={setSkillsOpen}
      />
      {editMode && <TweaksPanel tweaks={tweaks} update={updateTweak} onClose={() => setEditMode(false)} />}
    </div>
        {!sidebarOpen && sidebarPeekOpen && (
          <div
            onMouseEnter={openSidebarPeek}
            onMouseLeave={scheduleSidebarPeekClose}
            style={{
              position: "absolute",
              top: 36,
              left: 12,
              bottom: 16,
              width: "calc(var(--sidebar-w) + 16px)",
              zIndex: 30,
            }}
          >
            <div style={{
              height: "100%",
              ...floatingShellStyle(),
            }}>
              <div style={{ height: "100%", padding: "0 8px" }}>
                <SidebarContent
                  recents={recents}
                  setRecents={setRecents}
                  activeChatId={activeChatId}
                  setActiveChatId={setActiveChatId}
                  setMessagesByChat={setMessagesByChat}
                  rootStyle={{ padding: "12px 4px" }}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function nowTime() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/* ============ SIDEBAR ============ */
function Sidebar({ open, setOpen, recents, setRecents, activeChatId, setActiveChatId, setMessagesByChat }) {
  return (
    <aside style={{
      background: "transparent",
      display: "flex", flexDirection: "column",
      overflow: "visible",
      minWidth: 0,
      minHeight: 0,
      paddingTop: 0,
      position: "relative",
      zIndex: open ? 1 : 40,
    }}>
      <SidebarOpen
        recents={recents}
        setRecents={setRecents}
        activeChatId={activeChatId}
        setActiveChatId={setActiveChatId}
        setMessagesByChat={setMessagesByChat}
      />
    </aside>
  );
}

function SidebarOpen({ recents, setRecents, activeChatId, setActiveChatId, setMessagesByChat }) {
  return (
    <SidebarContent
      recents={recents}
      setRecents={setRecents}
      activeChatId={activeChatId}
      setActiveChatId={setActiveChatId}
      setMessagesByChat={setMessagesByChat}
    />
  );
}

function SidebarContent({ recents, setRecents, activeChatId, setActiveChatId, setMessagesByChat, rootStyle }) {
  const [q, setQ] = useState("");
  const filtered = recents.filter(r => r.title.toLowerCase().includes(q.toLowerCase()));
  const projects = INITIAL_PROJECTS;

  const handleNewChat = () => {
    const newId = `c-${Date.now()}`;
    const newChat = { id: newId, title: EMPTY_STATE_CHAT_TITLE, time: "刚刚", project: DEFAULT_PROJECT_NAME };
    setRecents(prev => [newChat, ...prev]);
    setActiveChatId(newId);
    if (setMessagesByChat) {
      setMessagesByChat((prev) => ({ ...prev, [newId]: [] }));
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, padding: "0 4px 0 12px", overflow: "visible", position: "relative", zIndex: 1, ...rootStyle }}>
      {/* New chat button */}
      <div style={{ padding: "0 0 8px" }}>
        <button
          onClick={handleNewChat}
          style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "8px 11px",
            width: "100%",
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            color: "var(--text-2)",
            fontSize: 13,
            cursor: "pointer",
            textAlign: "left",
            transition: "background .12s",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "rgba(0,0,0,0.03)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "var(--surface)"; }}
        >
          <Icon name="message-circle-plus" size={14} stroke={SIDEBAR_ICON_STROKE} color="#131313" />
          <span style={{ flex: 1, color: "var(--text)" }}>快速对话</span>
        </button>
      </div>

      {/* scroll area */}
      <div
        className="sidebar-scroll"
        style={{
          flex: 1,
          width: "calc(100% + 18px)",
          marginLeft: -8,
          marginRight: -10,
          paddingLeft: 8,
          paddingRight: 10,
          overflowY: "auto",
          overflowX: "visible",
        }}
      >
        {/* Projects - project folders */}
        <SidebarSection
          label="Projects"
          action={
            <button
              style={{
                display: "grid", placeItems: "center",
                width: 20, height: 20, borderRadius: 4,
                padding: 0,
                color: "#6E7481", background: "transparent", border: "none",
                cursor: "pointer",
                transition: "background .12s",
                lineHeight: 0,
              }}
              title="新建项目"
              onMouseEnter={e => { e.currentTarget.style.background = "rgba(0,0,0,0.05)"; }}
              onMouseLeave={e => { e.currentTarget.style.background = "transparent"; }}
            >
              <Icon name="plus" size={14} stroke={1.6} color="#6E7481" />
            </button>
          }
        >
          {projects.map(p => (
            <SidebarRow key={p.id} label={p.name} />
          ))}
        </SidebarSection>

        {/* Recents - conversations grouped by date */}
        <SidebarSection label="Recents">
          <div style={{ paddingTop: 4 }}>
          {(() => {
            const groups = { today: [], yesterday: [], lastWeek: [] };
            filtered.forEach(r => {
              if (r.time === "昨天") groups.yesterday.push(r);
              else if (["周一","周二","周三","周四","周五","周六","周日"].includes(r.time)) groups.lastWeek.push(r);
              else if (/\d{1,2}\s*月/.test(r.time)) groups.lastWeek.push(r);
              else groups.today.push(r);
            });
            return (
              <>
                {groups.today.map(r => (
                  <SidebarRow
                    key={r.id}
                    active={r.id === activeChatId}
                    onClick={() => setActiveChatId(r.id)}
                    label={r.title}
                  />
                ))}
                {groups.yesterday.length > 0 && (
                  <>
                    <SidebarSubheader label="Yesterday" />
                    {groups.yesterday.map(r => (
                      <SidebarRow
                        key={r.id}
                        active={r.id === activeChatId}
                        onClick={() => setActiveChatId(r.id)}
                        label={r.title}
                      />
                    ))}
                  </>
                )}
                {groups.lastWeek.length > 0 && (
                  <>
                    <SidebarSubheader label="Last Week" />
                    {groups.lastWeek.map(r => (
                      <SidebarRow
                        key={r.id}
                        active={r.id === activeChatId}
                        onClick={() => setActiveChatId(r.id)}
                        label={r.title}
                      />
                    ))}
                  </>
                )}
              </>
            );
          })()}
          </div>
        </SidebarSection>
      </div>

      {/* Bottom nav */}
      <div style={{
        padding: "8px 0 0",
        display: "flex", flexDirection: "column", gap: 1,
      }}>
        <SidebarRow icon={<Icon name="book" size={14} stroke={SIDEBAR_ICON_STROKE} color="#131313" />} label="知识库" />
        <SidebarRow icon={<Icon name="sparkles" size={14} stroke={SIDEBAR_ICON_STROKE} color="#131313" />} label="技能库" />
        <SidebarRow icon={<Icon name="clock-3" size={14} stroke={SIDEBAR_ICON_STROKE} color="#131313" />} label="定时任务" />
        <SidebarRow icon={<Icon name="settings" size={14} stroke={SIDEBAR_ICON_STROKE} color="#131313" />} label="设置" />
      </div>
    </div>
  );
}

function SidebarSection({ label, children, action }) {
  const [open, setOpen] = useState(true);

  return (
    <div style={{ marginBottom: 12, position: "relative", overflow: "visible" }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "6px 8px 4px 10px",
      }}>
        <button
          onClick={() => setOpen((v) => !v)}
          style={{
            display: "inline-flex",
            alignItems: "center",
          gap: 4,
          padding: 0,
          border: "none",
          background: "transparent",
          ...sectionLabelStyle(),
        }}
      >
          <span>{typeof label === "string" ? label.toUpperCase() : label}</span>
          <Icon
            name="chevRight"
            size={DISCLOSURE_CHEVRON_SIZE}
            stroke={DISCLOSURE_CHEVRON_STROKE}
            style={{
              transform: open ? "rotate(90deg)" : "none",
              transition: "transform .15s",
              color: DISCLOSURE_CHEVRON_COLOR,
            }}
          />
        </button>
        {action && <div style={{ display: "grid", placeItems: "center", width: 20, height: 20 }}>{action}</div>}
      </div>
      {open && <div style={{ display: "flex", flexDirection: "column", gap: 0, overflow: "visible" }}>{children}</div>}
    </div>
  );
}

function SidebarSubheader({ label }) {
  return (
    <div style={{
      padding: "8px 10px 4px",
      fontSize: DESIGN_TOKENS.type.sm, fontWeight: 500,
      color: SIDEBAR_SECTION_COLOR,
      opacity: 1,
    }}>
      {label}
    </div>
  );
}

function SidebarRow({ icon, label, trailing, sub, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={sidebarRowStyle(active)}
      onMouseEnter={e => !active && (e.currentTarget.style.background = "rgba(0,0,0,0.03)")}
      onMouseLeave={e => !active && (e.currentTarget.style.background = "transparent")}
    >
      {icon && (
        <span style={{ display: "grid", placeItems: "center", width: 16, color: TEXT_PRIMARY_COLOR }}>
          {icon}
        </span>
      )}
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
        {sub && <span style={{ display: "block", fontSize: 10.5, color: "var(--text-3)", marginTop: 1, fontWeight: 400 }}>{sub}</span>}
      </span>
      {trailing}
    </button>
  );
}

/* ============ MAIN CHAT ============ */
function Main({ activeChat, isEmptyState, messages, thinking, showStopControl, input, onInputChange, onKey, send, onStopGeneration, slashOpen, atOpen, setSlashOpen, setAtOpen, setInput, contextOpen, setContextOpen, messagesEndRef, inputRef, showToolTimings, emptyStateSuggestions }) {
  return (
    <section style={panelShellStyle({
      display: "flex", flexDirection: "column",
      height: "100%",
      minWidth: 0,
      minHeight: 0,
    })}>
      {/* Top bar */}
      <header style={panelHeaderStyle(isEmptyState ? { borderBottom: "none" } : {})}>
        {!isEmptyState && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <Icon name="fileText" size={14} />
            <span style={{ fontSize: 14, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {activeChat?.title || "英伟达 Q4 财报分析"}
            </span>
            {activeChat?.project && (
              <span style={{
                display: "inline-flex",
                alignItems: "center",
                height: 22,
                padding: "0 10px",
                borderRadius: 999,
                background: "#F7F8FA",
                border: "1px solid #E6E7E9",
                fontSize: 11.5,
                color: "#6E7481",
                whiteSpace: "nowrap",
              }}>
                {activeChat.project}
              </span>
            )}
          </div>
        )}
      </header>

      {/* Messages */}
      <div ref={messagesEndRef} className="chat-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "28px 0" }}>
        <div style={{ maxWidth: 760, margin: "0 auto", padding: "0 24px" }}>
          {isEmptyState ? (
            <ChatEmptyState
              suggestions={emptyStateSuggestions}
              onStart={(value) => send(value)}
            />
          ) : (
            messages.map((m, index) => <Message key={m.id} msg={m} index={index} showToolTimings={showToolTimings} />)
          )}
          {thinking && <ThinkingIndicator />}
        </div>
      </div>

      {/* Composer */}
      <Composer
        showStopControl={showStopControl}
        input={input}
        onInputChange={onInputChange}
        onKey={onKey}
        send={send}
        onStopGeneration={onStopGeneration}
        slashOpen={slashOpen}
        atOpen={atOpen}
        setSlashOpen={setSlashOpen}
        setAtOpen={setAtOpen}
        setInput={setInput}
        inputRef={inputRef}
        placeholder={isEmptyState ? "输入你的研究问题，回车即可开始一段新对话" : "输入你的研究问题…  使用 @Skill 调用能力，使用 / 查看命令"}
      />
    </section>
  );
}

function ChatEmptyState({ suggestions, onStart }) {
  return (
    <div style={{
      minHeight: "100%",
      display: "grid",
      placeItems: "center",
      padding: "36px 0 44px",
    }}>
      <div style={{ width: "100%", maxWidth: 560, textAlign: "center", transform: "translateY(40px)" }}>
        <div style={{ marginBottom: 40 }}>
        <div style={{
          width: 78,
          height: 78,
          margin: "0 auto",
          borderRadius: 24,
          border: "1px solid #E6E7E9",
          background: "linear-gradient(180deg, #FFFFFF 0%, #F7F8FA 100%)",
          display: "grid",
          placeItems: "center",
          boxShadow: "0 16px 40px -26px rgba(17,24,39,0.28)",
        }}>
          <Icon name="messageCirclePlus" size={28} stroke={1.8} color="#131313" />
        </div>
        <div style={{
          marginTop: 11,
          fontSize: 16,
          lineHeight: 1.6,
          color: "#131313",
        }}>
          这里会展示你和 Agent 围绕当前研究主题的提问、分析与产出。
        </div>
        </div>
        <div style={{ width: "100%", display: "flex", alignItems: "center", gap: 12, color: "#6E7481" }}>
          <span style={{ flex: 1, height: 1, background: "#E6E7E9" }} />
          <span style={{ fontSize: 14, lineHeight: 1 }}>快速对话</span>
          <span style={{ flex: 1, height: 1, background: "#E6E7E9" }} />
        </div>
        <div style={{ marginTop: 20, display: "grid", gap: 10 }}>
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              onClick={() => onStart(suggestion)}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "14px 16px",
                textAlign: "left",
                background: "#FFFFFF",
                border: "1px solid #E6E7E9",
                borderRadius: 12,
                color: "#131313",
                boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
                transition: "transform .12s ease, box-shadow .12s ease, border-color .12s ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = "translateY(-1px)";
                e.currentTarget.style.boxShadow = "0 1px 2px rgba(0,0,0,0.05)";
                e.currentTarget.style.borderColor = "#D4D4D8";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = "translateY(0)";
                e.currentTarget.style.boxShadow = "0 1px 2px rgba(0,0,0,0.05)";
                e.currentTarget.style.borderColor = "#E6E7E9";
              }}
            >
              <span style={{
                width: 28,
                height: 28,
                borderRadius: 9,
                background: "#F7F8FA",
                display: "grid",
                placeItems: "center",
                flexShrink: 0,
              }}>
                <Icon name="arrow" size={13} stroke={1.8} color="#6E7481" />
              </span>
              <span style={{ flex: 1, fontSize: 13.5, lineHeight: 1.5 }}>{suggestion}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ============ MESSAGE ============ */
function Message({ msg, index, showToolTimings }) {
  if (msg.role === "user") {
    const leftAligned = msg.layout === "left";
    const plain = msg.plain === true;
    const attachmentsAbove = msg.attachmentPlacement === "above" && msg.attachments?.length > 0;
    return (
      <div style={{ display: "flex", justifyContent: leftAligned ? "flex-start" : "flex-end", marginBottom: 26 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: leftAligned ? "flex-start" : "flex-end", maxWidth: leftAligned ? "86%" : "78%", minWidth: leftAligned ? 360 : "auto" }}>
          {attachmentsAbove && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 12, justifyContent: leftAligned ? "flex-start" : "flex-end" }}>
              {msg.attachments.map((file) => <MessageAttachment key={file.id} file={file} />)}
            </div>
          )}
          <div style={{
            width: leftAligned ? "100%" : "auto",
            padding: leftAligned ? "14px 16px" : index === 0 ? "12px 14px" : "0",
            background: !plain && (leftAligned || index === 0) ? "#F7F8FA" : "transparent",
            borderRadius: leftAligned || index === 0 ? 12 : 0,
            border: leftAligned && !plain ? "1px solid #ECEEF1" : "none",
            fontSize: 13.5, lineHeight: 1.6,
            color: "#131313",
            textAlign: leftAligned ? "left" : "right",
          }}>
            {msg.text}
            {!attachmentsAbove && msg.attachments?.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 12 }}>
                {msg.attachments.map((file) => <MessageAttachment key={file.id} file={file} />)}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ marginBottom: 26 }}>
      <div style={{ minWidth: 0 }}>
        {msg.text && <MessageText text={msg.text} streaming={msg.streaming} />}
        {msg.tools && (
          <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
            {msg.tools.map(t => <ToolCall key={t.id} tool={t} showToolTimings={showToolTimings} />)}
          </div>
        )}
        {msg.data?.kind === "revenue-table" && <RevenueTable rows={msg.data.rows} />}
        {!msg.tools && msg.text && !msg.streaming && <MessageActions />}
      </div>
    </div>
  );
}

function MessageAttachment({ file }) {
  return (
    <div style={{
      minWidth: 184,
      padding: "10px 12px",
      borderRadius: 10,
      border: "1px solid #E6E7E9",
      background: "#FFFFFF",
      boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <FileBadge kind={file.kind} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 12.5, color: "#131313", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {file.name}
          </div>
          <div style={{ marginTop: 4, fontSize: 11, color: "#6E7481" }}>
            {file.size}  {file.status}
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageText({ text, streaming = false }) {
  // minimal markdown: **bold**, lists, line breaks
  const lines = text.split("\n");
  return (
    <div style={{ fontSize: 13.5, lineHeight: 1.7, color: "#131313" }}>
      {lines.map((line, i) => {
        if (!line.trim()) return <div key={i} style={{ height: 6 }} />;
        if (line.startsWith("- ")) {
          return (
            <div key={i} style={{ display: "flex", gap: 8, paddingLeft: 4 }}>
              <span style={{ color: "#6E7481" }}>•</span>
              <span dangerouslySetInnerHTML={{ __html: fmt(line.slice(2)) }} />
            </div>
          );
        }
        return (
          <div key={i}>
            <span dangerouslySetInnerHTML={{ __html: fmt(line) }} />
          </div>
        );
      })}
    </div>
  );
}

function fmt(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, '<b style="color:#131313;font-weight:600">$1</b>')
    .replace(/`([^`]+)`/g, '<code style="font-family:var(--mono);font-size:12px;background:var(--surface-2);padding:1px 5px;border-radius:4px;border:1px solid var(--border)">$1</code>');
}

function MessageActions() {
  const btn = {
    display: "grid", placeItems: "center",
    width: 26, height: 26, borderRadius: 6,
    color: "var(--text-3)",
    transition: "all .12s",
  };
  return (
    <div style={{ display: "flex", gap: 2, marginTop: 8, marginLeft: -4 }}>
      {[
        { icon: "copy", title: "复制" },
      ].map((a, i) => (
        <button key={i} title={a.title} style={btn}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--surface-2)"; e.currentTarget.style.color = "var(--text-2)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-3)"; }}
        >
          <Icon name={a.icon} size={13} />
        </button>
      ))}
    </div>
  );
}

/* ============ TOOL CALL ============ */
function ToolCall({ tool, showToolTimings }) {
  const [open, setOpen] = useState(tool.defaultOpen ?? tool.status === "running");
  const statusMap = {
    done: { label: "完成", color: "#131313", bg: "rgba(83, 188, 118, 0.15)", border: "rgba(83, 188, 118, 0.5)" },
    running: { label: "运行中", color: "#131313", bg: "rgba(114, 92, 249, 0.08)", border: "#D9D9DD" },
    queued: { label: "排队", color: "var(--text-3)", bg: "var(--surface-2)" },
    error: { label: "失败", color: "#B42318", bg: "rgba(253,165,165,0.2)", border: "#FCA5A5" },
  };
  const st = statusMap[tool.status] || statusMap.done;
  const isSkill = tool.kind === "skill" || String(tool.name || "").toLowerCase().startsWith("skill:");
  const cardTitle = tool.title || (isSkill ? String(tool.name || "").replace(/^skill:\s*/i, "") : tool.name);
  const summary = tool.label || tool.detail;
  const hasDetail = Boolean(tool.detail || (tool.sections && tool.sections.length));
  const showRunningSpinner = isSkill && tool.status === "running";

  return (
    <div style={{
      border: `1px solid ${tool.status === "error" ? "#FECACA" : "#F3F4F6"}`,
      borderRadius: 8,
      background: tool.status === "error" ? "#FFF8F7" : "#F7F8FA",
      overflow: "hidden",
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: "100%",
          display: "flex", alignItems: "center", gap: 8,
          padding: "9px 12px",
          textAlign: "left",
          background: "transparent",
        }}
      >
        <Icon name="chevRight" size={DISCLOSURE_CHEVRON_SIZE} stroke={DISCLOSURE_CHEVRON_STROKE}
          style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s", color: DISCLOSURE_CHEVRON_COLOR, flexShrink: 0 }}
        />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ minWidth: 0, flex: 1, fontFamily: "var(--mono)", fontSize: 12, fontWeight: 500, color: "#131313" }}>
            {isSkill ? "Skill:" : "Tool Call:"} {cardTitle}
          </div>
          {summary && (
            <div style={{ fontSize: 12, color: "#6E7481", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 2 }}>
              {summary}
            </div>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: showRunningSpinner ? 12 : 0, flexShrink: 0 }}>
          {showRunningSpinner && <Spinner size={8} />}
          <span style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            height: 17,
            fontSize: 11, fontWeight: 400,
            color: st.color,
            padding: "0 8px",
            borderRadius: 4,
            background: st.bg,
            border: st.border ? `1px solid ${st.border}` : "1px solid transparent",
            boxSizing: "border-box",
            flexShrink: 0,
          }}>
            {st.label}
          </span>
        </div>
      </button>
      {open && hasDetail && (
        <div style={{
          borderTop: `1px solid ${tool.status === "error" ? "#FFF1F1" : "#F3F4F6"}`,
          padding: "10px 12px 12px 32px",
          color: "#6E7481",
          background: tool.status === "error" ? "#FFF8F7" : "#F7F8FA",
        }}>
          {tool.sections?.length ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {tool.sections.map((section, index) => (
                <div key={`${tool.id}-section-${index}`}>
                  <div style={{ fontSize: 11, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 6, color: "#9CA3AF" }}>
                    {section.label}
                  </div>
                  {section.code ? (
                    <div style={{
                      padding: "10px 12px",
                      borderRadius: 8,
                      background: "#FFFFFF",
                      border: "1px solid #E5E7EB",
                      fontFamily: "var(--mono)",
                      fontSize: 11.5,
                      lineHeight: 1.65,
                      whiteSpace: "pre-wrap",
                      color: tool.status === "error" && section.label === "错误" ? "#B42318" : "#4B5563",
                    }}>
                      {section.code}
                    </div>
                  ) : (
                    <div style={{ fontSize: 12.5, lineHeight: 1.65, color: "#4B5563", whiteSpace: "pre-wrap" }}>
                      {section.value}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div style={{
              fontFamily: "var(--mono)",
              fontSize: 11.5,
              lineHeight: 1.6,
              whiteSpace: "pre-wrap",
            }}>
              {tool.detail}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Spinner({ size = 10 }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: "50%",
      border: "1.5px solid var(--border-strong)",
      borderTopColor: "var(--accent)",
      animation: "spin .8s linear infinite",
      flexShrink: 0,
    }} />
  );
}

function ThinkingIndicator() {
  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "10px 0" }}>
        <span className="dot" style={dotStyle(0)} />
        <span className="dot" style={dotStyle(0.15)} />
        <span className="dot" style={dotStyle(0.3)} />
      </div>
    </div>
  );
}
function dotStyle(delay) {
  return {
    width: 6, height: 6, borderRadius: "50%",
    background: "var(--text-3)",
    animation: "bounce 1.2s infinite ease-in-out",
    animationDelay: delay + "s",
  };
}

/* ============ REVENUE TABLE ============ */
function RevenueTable({ rows }) {
  return (
    <div style={{
      marginTop: 14,
      border: "1px solid #F3F4F6",
      borderRadius: 8,
      overflow: "hidden",
      background: "var(--surface)",
    }}>
      <div style={{
        padding: "9px 14px",
        fontSize: 11, fontWeight: 600, letterSpacing: "0.06em",
        color: "#6E7481",
        borderBottom: "1px solid #F3F4F6",
        background: "#F7F8FA",
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <Icon name="chart" size={12} color="#6E7481" />
        分业务营收拆解（$B）
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr style={{ color: "#6E7481", fontSize: 11 }}>
            <th style={thTd()}>业务板块</th>
            <th style={{ ...thTd(), textAlign: "right" }}>FY25 Q4</th>
            <th style={{ ...thTd(), textAlign: "right" }}>FY25 Q3</th>
            <th style={{ ...thTd(), textAlign: "right" }}>YoY</th>
            <th style={{ ...thTd(), textAlign: "right" }}>QoQ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderTop: "1px solid #F3F4F6" }}>
              <td style={{ ...thTd(), fontWeight: 500 }}>{r.seg}</td>
              <td style={{ ...thTd(), textAlign: "right", fontFamily: "var(--sans)" }}>{r.q4.toFixed(2)}</td>
              <td style={{ ...thTd(), textAlign: "right", fontFamily: "var(--sans)", color: "var(--text-2)" }}>{r.q3.toFixed(2)}</td>
              <td style={{ ...thTd(), textAlign: "right", fontFamily: "var(--sans)", color: r.yoy.startsWith("−") ? FINANCE_DOWN_COLOR : FINANCE_UP_COLOR }}>{r.yoy}</td>
              <td style={{ ...thTd(), textAlign: "right", fontFamily: "var(--sans)", color: r.qoq.startsWith("−") ? FINANCE_DOWN_COLOR : FINANCE_UP_COLOR }}>{r.qoq}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function thTd() { return { padding: "9px 14px", textAlign: "left", fontWeight: 500 }; }

/* ============ COMPOSER ============ */
const SLASH_COMMANDS = [
  { cmd: "/研报撰写模板", desc: "按标准研报结构生成分析" },
  { cmd: "/财务建模", desc: "调用 DCF / 敏感性分析模型" },
  { cmd: "/竞品对比", desc: "多公司关键指标横向对比" },
  { cmd: "/图表生成", desc: "把数据转为折线/柱状图" },
  { cmd: "/周报", desc: "周度要点提炼与汇总" },
];

const AT_ITEMS = [
  { icon: "book", label: "知识库 / 英伟达 2024 年报", kind: "kb" },
  { icon: "book", label: "知识库 / 分析师纪要合集", kind: "kb" },
  { icon: "globe", label: "网页 / 实时行情 (Yahoo Finance)", kind: "web" },
  { icon: "database", label: "数据 / Wind 终端", kind: "data" },
  { icon: "file", label: "上传 / Q4 财报.pdf", kind: "file" },
];

const MODEL_OPTIONS = [
  { value: "GPT-5.4", desc: "适合复杂研究与写作" },
  { value: "GPT-4.1", desc: "更偏稳定的通用分析" },
  { value: "o4-mini", desc: "更快的轻量处理" },
];

const REASONING_OPTIONS = [
  { value: "低", desc: "更快返回结果" },
  { value: "中", desc: "平衡速度与质量" },
  { value: "高", desc: "更深入的推理" },
];

function Composer({ showStopControl, input, onInputChange, onKey, send, onStopGeneration, slashOpen, atOpen, setSlashOpen, setAtOpen, setInput, inputRef, placeholder }) {
  const [selectedModel, setSelectedModel] = useState("GPT-5.4");
  const [selectedReasoning, setSelectedReasoning] = useState("中");
  const [openMenu, setOpenMenu] = useState(null);
  const menuRef = useRef(null);

  useEffect(() => {
    const onPointerDown = (e) => {
      if (!menuRef.current?.contains(e.target)) {
        setOpenMenu(null);
        setSlashOpen(false);
        setAtOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [setAtOpen, setSlashOpen]);

  return (
    <div style={{ padding: "10px 20px 16px", background: "var(--surface)", position: "relative", zIndex: 6 }}>
      <div ref={menuRef} style={{ maxWidth: 760, margin: "0 auto", position: "relative", zIndex: 2 }}>
        {slashOpen && (
          <Popover>
            {SLASH_COMMANDS.map((s, i) => (
              <PopoverItem key={i} onClick={() => { setInput(s.cmd + " "); setSlashOpen(false); inputRef.current?.focus(); }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={composerMenuTitleStyle({ fontFamily: "var(--mono)" })}>{s.cmd}</span>
                  <span style={composerMenuDescriptionStyle()}>{s.desc}</span>
                </span>
              </PopoverItem>
            ))}
          </Popover>
        )}
        {atOpen && (
          <Popover title="引用上下文">
            {AT_ITEMS.map((s, i) => (
              <PopoverItem key={i} onClick={() => { setInput(input + s.label.split(" / ")[1] + " "); setAtOpen(false); inputRef.current?.focus(); }}>
                <span style={{ width: 16, display: "grid", placeItems: "center", color: TEXT_SECONDARY_COLOR, flexShrink: 0 }}>
                  <Icon name={s.icon} size={12} stroke={1.9} />
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={composerMenuTitleStyle()}>{s.label}</span>
                  <span style={composerMenuDescriptionStyle()}>{s.kind === "kb" ? "来自知识库上下文" : s.kind === "web" ? "来自网页与实时数据" : s.kind === "data" ? "来自结构化数据源" : "来自已上传文件"}</span>
                </span>
              </PopoverItem>
            ))}
          </Popover>
        )}

        <div style={{
          padding: "12px 8px 8px",
          transition: "border-color .15s",
          ...panelShellStyle({
            borderRadius: DESIGN_TOKENS.radius.panel,
            boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
            overflow: "visible",
          }),
        }}>
          <textarea
            className="composer-textarea"
            ref={inputRef}
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={onKey}
            placeholder={placeholder}
            rows={2}
            style={{
              width: "100%",
              border: "none", outline: "none",
              background: "transparent", resize: "none",
              fontSize: 13, lineHeight: 1.55,
              color: "var(--text)",
              minHeight: 48,
              fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
            <TooltipWrap tooltip="上传文件" align="left">
              <button
                aria-label="上传文件"
                style={composerIconButtonStyle()}
                onMouseEnter={e => e.currentTarget.style.background = COMPOSER_HOVER_BG}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                <Icon name="paperclip" size={15} stroke={1.9} color="#131313" />
              </button>
            </TooltipWrap>
            <ComposerBtn
              icon="sparkles"
              label="Skill"
              iconSize={15}
              iconStroke={1.9}
              color="#131313"
              fontSize={12}
              iconOnly
              active={slashOpen}
              tooltip="Skill"
              onClick={() => {
                setOpenMenu(null);
                setAtOpen(false);
                setSlashOpen((prev) => !prev);
                inputRef.current?.focus();
              }}
            />
            <div style={{ flex: 1 }} />
            <ComposerSelect
              icon="model"
              label={selectedModel}
              tooltip="模型选择"
              open={openMenu !== null}
              onClick={() => {
                setSlashOpen(false);
                setAtOpen(false);
                setOpenMenu(openMenu ? null : "model");
              }}
            >
              <ComposerModelMenu
                selectedModel={selectedModel}
                selectedReasoning={selectedReasoning}
                menuView={openMenu}
                onOpenReasoning={() => setOpenMenu("reasoning")}
                onSelectModel={(value) => {
                  setSelectedModel(value);
                  setOpenMenu(null);
                }}
                onSelectReasoning={(value) => {
                  setSelectedReasoning(value);
                  setOpenMenu(null);
                }}
              />
            </ComposerSelect>
            <button
              onClick={() => {
                if (showStopControl) {
                  onStopGeneration?.();
                  return;
                }
                send();
              }}
              style={{
                width: 32, height: 32, borderRadius: 10,
                background: showStopControl ? "#5B4EF7" : "var(--accent)",
                color: "#fff",
                display: "grid", placeItems: "center",
                transition: "all .15s",
                marginLeft: 4,
                boxShadow: showStopControl ? "0 1px 2px rgba(0,0,0,0.05)" : "none",
              }}
            >
              {showStopControl ? (
                <span style={{
                  width: 12,
                  height: 12,
                  borderRadius: 2,
                  background: "#FFFFFF",
                  display: "block",
                }} />
              ) : (
                <Icon name="heroArrowUp" size={13} stroke={2} />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ComposerSelect({ icon, label, tooltip, onClick, open, children }) {
  return (
    <TooltipWrap tooltip={tooltip}>
      <div style={{ position: "relative", zIndex: open ? 8 : 1 }}>
      <button
        onClick={onClick}
        style={{
          display: "flex", alignItems: "center", gap: 5,
          height: 28,
          padding: "0 8px",
          borderRadius: DESIGN_TOKENS.radius.card,
          color: DESIGN_TOKENS.color.textPrimary,
          fontSize: DESIGN_TOKENS.type.bodySm,
          background: open ? COMPOSER_HOVER_BG : "transparent",
          transition: "background .12s",
        }}
        onMouseEnter={e => !open && (e.currentTarget.style.background = COMPOSER_HOVER_BG)}
        onMouseLeave={e => !open && (e.currentTarget.style.background = "transparent")}
      >
        {icon === "model" && (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M12 3a9 9 0 0 1 0 18" opacity="0.35" />
            <path d="M12 3a9 9 0 0 0 0 18" />
          </svg>
        )}
        <span>{label}</span>
        <Icon
          name="chevDown"
          size={DISCLOSURE_CHEVRON_SIZE}
          stroke={DISCLOSURE_CHEVRON_STROKE}
          style={{ color: DISCLOSURE_CHEVRON_COLOR }}
        />
      </button>
      {open && children}
      </div>
    </TooltipWrap>
  );
}

function ComposerModelMenu({ selectedModel, selectedReasoning, menuView, onOpenReasoning, onSelectModel, onSelectReasoning }) {
  const selectedReasoningOption = REASONING_OPTIONS.find((option) => option.value === selectedReasoning) || REASONING_OPTIONS[0];
  return (
    <div style={{
      position: "absolute",
      right: 0,
      bottom: "calc(100% + 8px)",
      zIndex: 20,
      overflow: "visible",
    }}>
      <div style={menuShellStyle({
        minWidth: 250,
      })}>
        <div style={composerMenuSectionStyle()}>
          {MODEL_OPTIONS.map((option) => {
            const active = option.value === selectedModel;
            return (
              <button
                key={option.value}
                onClick={() => onSelectModel(option.value)}
                style={composerMenuItemStyle(active && menuView === "model")}
                onMouseEnter={e => !active && (e.currentTarget.style.background = COMPOSER_HOVER_BG)}
                onMouseLeave={e => !active && (e.currentTarget.style.background = "transparent")}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={composerMenuTitleStyle()}>{option.value}</span>
                  <span style={composerMenuDescriptionStyle()}>{option.desc}</span>
                </span>
                <span style={{ width: 16, display: "grid", placeItems: "center", color: active ? TEXT_PRIMARY_COLOR : "transparent", flexShrink: 0 }}>
                  <Icon name="check" size={13} stroke={2} />
                </span>
              </button>
            );
          })}
        </div>
        <div style={{ borderTop: `1px solid ${DESIGN_TOKENS.color.divider}`, ...composerMenuSectionStyle() }}>
          <button
            onClick={onOpenReasoning}
            style={composerMenuItemStyle(menuView === "reasoning")}
            onMouseEnter={e => menuView !== "reasoning" && (e.currentTarget.style.background = COMPOSER_HOVER_BG)}
            onMouseLeave={e => menuView !== "reasoning" && (e.currentTarget.style.background = "transparent")}
          >
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={composerMenuTitleStyle()}>{selectedReasoningOption.value}</span>
              <span style={composerMenuDescriptionStyle()}>{selectedReasoningOption.desc}</span>
            </span>
            <Icon
              name="chevRight"
              size={DISCLOSURE_CHEVRON_SIZE}
              stroke={DISCLOSURE_CHEVRON_STROKE}
              style={{ color: DISCLOSURE_CHEVRON_COLOR }}
            />
          </button>
        </div>
      </div>
      {menuView === "reasoning" && (
        <div style={{
          position: "absolute",
          right: "calc(100% + 8px)",
          bottom: -1,
          minWidth: 250,
          zIndex: 21,
          ...menuShellStyle(),
        }}>
          <div style={composerMenuSectionStyle()}>
            {REASONING_OPTIONS.map((option) => {
              const active = option.value === selectedReasoning;
              return (
                <button
                  key={option.value}
                  onClick={() => onSelectReasoning(option.value)}
                  style={composerMenuItemStyle(active)}
                  onMouseEnter={e => !active && (e.currentTarget.style.background = COMPOSER_HOVER_BG)}
                  onMouseLeave={e => !active && (e.currentTarget.style.background = "transparent")}
                >
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={composerMenuTitleStyle()}>{option.value}</span>
                    <span style={composerMenuDescriptionStyle()}>{option.desc}</span>
                  </span>
                  <span style={{ width: 16, display: "grid", placeItems: "center", color: active ? TEXT_PRIMARY_COLOR : "transparent", flexShrink: 0 }}>
                    <Icon name="check" size={13} stroke={2} />
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function TooltipBubble({ children, side = "top", align = "center", offset = 10 }) {
  const verticalPosition = side === "bottom"
    ? { top: `calc(100% + ${offset}px)` }
    : { bottom: `calc(100% + ${offset}px)` };
  const horizontalPosition = align === "left"
    ? { left: 0, transform: "none" }
    : align === "right"
      ? { right: 0, transform: "none" }
      : { left: "50%", transform: "translateX(-50%)" };
  return (
    <div style={{
      position: "absolute",
      ...verticalPosition,
      ...horizontalPosition,
      padding: "10px 14px",
      borderRadius: 4,
      background: "#39414D",
      color: "#FFFFFF",
      fontSize: 12,
      lineHeight: 1,
      whiteSpace: "nowrap",
      pointerEvents: "none",
      zIndex: 12,
    }}>
      {children}
    </div>
  );
}

function TooltipWrap({ tooltip, side = "top", align = "center", offset = 10, children }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      style={{ position: "relative", display: "inline-flex" }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {hovered && tooltip && <TooltipBubble side={side} align={align} offset={offset}>{tooltip}</TooltipBubble>}
      {children}
    </div>
  );
}

function ComposerBtn({ icon, label, tooltip, onClick, iconSize = 13, iconStroke = 1.6, color = "var(--text-2)", fontSize = 12, iconOnly = false, active = false }) {
  return (
    <TooltipWrap tooltip={tooltip}>
      <button
        onClick={onClick}
        aria-label={tooltip || label}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: iconOnly ? 0 : 5,
          width: iconOnly ? 28 : "auto",
          height: 28,
          padding: iconOnly ? 0 : "4px 9px",
          borderRadius: 8,
          color,
          fontSize,
          background: active ? COMPOSER_HOVER_BG : "transparent",
          transition: "background .12s",
        }}
        onMouseEnter={e => e.currentTarget.style.background = COMPOSER_HOVER_BG}
        onMouseLeave={e => e.currentTarget.style.background = active ? COMPOSER_HOVER_BG : "transparent"}
      >
        <Icon name={icon} size={iconSize} stroke={iconStroke} />
        {!iconOnly && <span>{label}</span>}
      </button>
    </TooltipWrap>
  );
}

function Popover({ title, children }) {
  return (
    <div style={menuShellStyle({
      position: "absolute",
      bottom: "calc(100% + 6px)",
      left: 0,
      minWidth: 320,
      maxWidth: 420,
      zIndex: 20,
      overflow: "visible",
    })}>
      {title ? <PopoverHeader>{title}</PopoverHeader> : null}
      <div style={composerMenuSectionStyle()}>{children}</div>
    </div>
  );
}
function PopoverHeader({ children }) {
  return (
    <div style={{
      ...composerMenuEyebrowStyle(),
    }}>{children}</div>
  );
}
function PopoverItem({ children, onClick }) {
  return (
      <button onClick={onClick}
      style={composerMenuItemStyle(false)}
      onMouseEnter={e => e.currentTarget.style.background = COMPOSER_HOVER_BG}
      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
    >
      {children}
    </button>
  );
}

/* ============ CONTEXT PANEL ============ */
function ContextPanel({ open, todos, toggleTodo, doneCount, todosOpen, setTodosOpen, filesOpen, setFilesOpen, uploadsOpen, setUploadsOpen, skillsOpen, setSkillsOpen }) {
  if (!open) return null;
  const toggleSection = (section) => {
    const nextOpen = section === "todos" ? !todosOpen : section === "files" ? !filesOpen : !uploadsOpen;
    setTodosOpen(section === "todos" ? nextOpen : false);
    setFilesOpen(section === "files" ? nextOpen : false);
    setUploadsOpen(section === "uploads" ? nextOpen : false);
  };
  return (
    <aside style={panelShellStyle({
      display: "flex", flexDirection: "column",
      minHeight: 0,
    })}>
      <div style={panelHeaderStyle()}>
        <span style={{ fontSize: 15, fontWeight: 500, color: "#131313" }}>Session Context</span>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "8px 8px 0" }}>
        <ContextSection
          open={todosOpen}
          onToggle={() => toggleSection("todos")}
          iconColor="#22C48B"
          icon="squareCheck" label="待办事项"
          meta={<span style={ctxMetaStyle()}>{doneCount}/{todos.length}</span>}
        >
          <div style={{ padding: "8px 14px" }}>
            {todos.map(t => (
              <button
                key={t.id}
                onClick={() => toggleTodo(t.id)}
                style={{
                  width: "100%",
                  display: "flex", alignItems: "center", gap: 9,
                  padding: "4px 0",
                  minHeight: 28,
                  textAlign: "left",
                  color: t.done ? TEXT_DISABLED_COLOR : TEXT_PRIMARY_COLOR,
                  fontSize: 12.5,
                  textDecoration: t.done ? "line-through" : "none",
                }}
              >
                <span style={{ width: 18, display: "grid", placeItems: "center", color: TEXT_SECONDARY_COLOR, flexShrink: 0 }}>
                  <Icon
                    name={t.done ? "checkboxFilled" : "checkbox"}
                    size={18}
                    stroke={t.done ? 1.7 : 1}
                    color={t.done ? TEXT_SECONDARY_COLOR : TEXT_DISABLED_COLOR}
                  />
                </span>
                <span style={{ lineHeight: "20px" }}>{t.text}</span>
              </button>
            ))}
          </div>
        </ContextSection>

        <ContextSection
          open={filesOpen}
          onToggle={() => toggleSection("files")}
          iconColor="#FF8A1F"
          icon="fileText" label="生成文件"
          meta={<span style={ctxMetaStyle()}>已选 {GENERATED_FILES.length}</span>}
        >
          <div style={{ padding: "12px 10px 12px" }}>
            {GENERATED_FILES.map(f => (
              <div key={f.id} style={fileRowStyle()}>
                <FileBadge kind={f.kind} />
                <span style={{ fontSize: 12.5, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                <span style={{ fontSize: 10.5, color: "#6E7481", fontFamily: "var(--sans)" }}>{f.size}</span>
              </div>
            ))}
          </div>
        </ContextSection>

        <ContextSection
          open={uploadsOpen}
          onToggle={() => toggleSection("uploads")}
          iconColor="#2F5BFF"
          icon="paperclip" label="上传文件"
          meta={<span style={ctxMetaStyle()}>{UPLOAD_FILES.length}</span>}
        >
          <div style={{ padding: "12px 10px 12px" }}>
            {UPLOAD_FILES.map(f => (
              <div key={f.id} style={fileRowStyle()}>
                <FileBadge kind="pdf" />
                <span style={{ fontSize: 12.5, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                <span style={{ fontSize: 10.5, color: "#6E7481", fontFamily: "var(--sans)" }}>{f.size}</span>
              </div>
            ))}
          </div>
        </ContextSection>
      </div>
    </aside>
  );
}

function ContextSection({ open, onToggle, icon, iconColor, label, meta, children }) {
  return (
    <div style={{
      margin: "0 0 8px",
      background: DESIGN_TOKENS.color.surfaceMuted,
      border: `1px solid ${DESIGN_TOKENS.color.divider}`,
      borderRadius: DESIGN_TOKENS.radius.shell,
      overflow: "hidden",
    }}>
      <button
        onClick={onToggle}
        style={{
          width: "100%",
          minHeight: 40,
          display: "flex", alignItems: "center", gap: 9,
          padding: "10px 14px",
          textAlign: "left",
          color: "var(--text)",
          background: "transparent",
        }}
      >
        <Icon name="chevRight" size={DISCLOSURE_CHEVRON_SIZE} stroke={DISCLOSURE_CHEVRON_STROKE}
          style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s", color: DISCLOSURE_CHEVRON_COLOR }}
        />
        <Icon name={icon} size={14} stroke={1.9} color={iconColor} />
        <span style={{ fontSize: 13, fontWeight: 500, color: "#131313" }}>{label}</span>
        <span style={{ marginLeft: "auto" }}>{meta}</span>
      </button>
      {open && (
        <div>
          <div style={{
            background: "#FFFFFF",
            borderTopLeftRadius: 12,
            borderTopRightRadius: 12,
            overflow: "hidden",
          }}>
            {children}
          </div>
        </div>
      )}
    </div>
  );
}

function ctxMetaStyle() {
  return {
    fontSize: 12, color: "#6E7481",
  };
}

function fileRowStyle() {
  return {
    display: "flex", alignItems: "center", gap: 9,
    padding: "6px 4px",
    borderRadius: 6,
    cursor: "pointer",
  };
}

function FileBadge({ kind }) {
  const map = {
    md:   { bg: "#eef2ff", fg: "#4338ca", label: "MD" },
    csv:  { bg: "#ecfdf5", fg: "#059669", label: "CSV" },
    xlsx: { bg: "#ecfdf5", fg: "#047857", label: "XLS" },
    img:  { bg: "#fef3c7", fg: "#b45309", label: "IMG" },
    pdf:  { bg: "#fee2e2", fg: "#b91c1c", label: "PDF" },
  };
  const s = map[kind] || map.md;
  return (
    <span style={{
      width: 26, height: 20, borderRadius: 4,
      background: s.bg, color: s.fg,
      display: "grid", placeItems: "center",
      fontSize: 9.5, fontWeight: 700, fontFamily: "var(--mono)",
      letterSpacing: "0.04em",
      flexShrink: 0,
    }}>{s.label}</span>
  );
}

function SkillPill({ name, status }) {
  const running = status === "运行中";
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "7px 10px",
      border: "1px solid var(--border)",
      borderRadius: 6,
      background: "var(--bg)",
    }}>
      <Icon name="sparkles" size={12} />
      <span style={{ fontSize: 12.5, flex: 1 }}>{name}</span>
      <span style={{
        fontSize: 10, fontWeight: 600,
        color: running ? "var(--warn)" : "var(--success)",
        background: running ? "var(--warn-soft)" : "var(--success-soft)",
        padding: "1px 6px", borderRadius: 3,
      }}>
        {status}
      </span>
    </div>
  );
}

/* ============ TWEAKS ============ */
function TweaksPanel({ tweaks, update, onClose }) {
  return (
    <div style={{
      position: "fixed", bottom: 20, right: 20,
      width: 280,
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: 12,
      boxShadow: "0 20px 40px -10px rgba(0,0,0,0.15)",
      zIndex: 100,
      padding: 14,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Tweaks</span>
        <button onClick={onClose} style={iconBtn()}><Icon name="x" size={13} /></button>
      </div>

      <TweakRow label="主题色">
        <div style={{ display: "flex", gap: 6 }}>
          {Object.entries(ACCENTS).map(([k, v]) => (
            <button key={k}
              onClick={() => update("accent", k)}
              style={{
                width: 22, height: 22, borderRadius: "50%",
                background: v.accent,
                border: tweaks.accent === k ? "2px solid var(--text)" : "2px solid transparent",
                outline: tweaks.accent === k ? "none" : "1px solid var(--border)",
                outlineOffset: -1,
              }}
            />
          ))}
        </div>
      </TweakRow>

      <TweakRow label="外观">
        <SegmentedControl
          value={tweaks.mode} options={[{ v: "light", l: "浅色" }, { v: "dark", l: "深色" }]}
          onChange={(v) => update("mode", v)}
        />
      </TweakRow>

      <TweakRow label="会话上下文">
        <SegmentedControl
          value={tweaks.contextOpen ? "on" : "off"} options={[{ v: "on", l: "显示" }, { v: "off", l: "隐藏" }]}
          onChange={(v) => update("contextOpen", v === "on")}
        />
      </TweakRow>

      <TweakRow label="工具耗时">
        <SegmentedControl
          value={tweaks.showToolTimings ? "on" : "off"} options={[{ v: "on", l: "显示" }, { v: "off", l: "隐藏" }]}
          onChange={(v) => update("showToolTimings", v === "on")}
        />
      </TweakRow>
    </div>
  );
}

function TweakRow({ label, children }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}>
      <span style={{ fontSize: 12, color: "var(--text-2)" }}>{label}</span>
      {children}
    </div>
  );
}

function SegmentedControl({ value, options, onChange }) {
  return (
    <div style={{ display: "flex", background: "var(--surface-2)", borderRadius: 6, padding: 2, border: "1px solid var(--border)" }}>
      {options.map(o => (
        <button key={o.v}
          onClick={() => onChange(o.v)}
          style={{
            padding: "3px 9px",
            fontSize: 11.5, fontWeight: 500,
            borderRadius: 4,
            background: value === o.v ? "var(--surface)" : "transparent",
            color: value === o.v ? "var(--text)" : "var(--text-2)",
            boxShadow: value === o.v ? "0 1px 2px rgba(0,0,0,0.05)" : "none",
          }}
        >{o.l}</button>
      ))}
    </div>
  );
}

/* ============ MISC ============ */
function iconBtn(small) {
  return {
    width: small ? 20 : 28, height: small ? 20 : 28,
    borderRadius: DESIGN_TOKENS.radius.md,
    display: "grid", placeItems: "center",
    color: "var(--text-2)",
    transition: "background .12s",
  };
}

function composerIconButtonStyle() {
  return {
    width: 28,
    height: 28,
    borderRadius: DESIGN_TOKENS.radius.card,
    display: "grid",
    placeItems: "center",
    background: "transparent",
    transition: "background .12s",
    flexShrink: 0,
  };
}

function kbdStyle() {
  return {
    fontFamily: "var(--mono)", fontSize: DESIGN_TOKENS.type.xs,
    padding: "1px 5px",
    border: "1px solid var(--border)",
    borderRadius: DESIGN_TOKENS.radius.xs,
    color: "var(--text-3)",
    background: "var(--surface)",
  };
}

/* Inject keyframes */
const styleEl = document.createElement("style");
styleEl.textContent = `
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes bounce { 0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; } 40% { transform: scale(1); opacity: 1; } }
button:hover { cursor: pointer; }
`;
document.head.appendChild(styleEl);

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
