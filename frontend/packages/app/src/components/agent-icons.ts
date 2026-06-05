import {
  BarChart3,
  Bot,
  BrainCircuit,
  BriefcaseBusiness,
  Code2,
  FileSearch,
  Globe2,
  Headphones,
  Lightbulb,
  Megaphone,
  Palette,
  PenTool,
  Scale,
  Search,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { createElement } from "react";
import type { Agent } from "@valuz/core";

const AGENT_ICON_KEYWORDS: Array<{
  icon: LucideIcon;
  keywords: string[];
}> = [
  {
    icon: BarChart3,
    keywords: [
      "分析",
      "数据",
      "报表",
      "图表",
      "analyst",
      "analysis",
      "data",
      "chart",
      "report",
    ],
  },
  {
    icon: Code2,
    keywords: [
      "代码",
      "开发",
      "工程",
      "程序",
      "code",
      "dev",
      "engineer",
      "program",
    ],
  },
  {
    icon: FileSearch,
    keywords: [
      "文档",
      "研究",
      "资料",
      "文件",
      "doc",
      "document",
      "research",
      "paper",
    ],
  },
  {
    icon: PenTool,
    keywords: [
      "写作",
      "文案",
      "编辑",
      "内容",
      "writer",
      "write",
      "copy",
      "content",
      "editor",
    ],
  },
  {
    icon: Megaphone,
    keywords: ["营销", "增长", "推广", "市场", "marketing", "growth", "sales"],
  },
  {
    icon: Palette,
    keywords: ["设计", "视觉", "美术", "ui", "design", "visual", "art"],
  },
  {
    icon: Headphones,
    keywords: ["客服", "支持", "服务", "support", "service", "customer"],
  },
  {
    icon: ShieldCheck,
    keywords: [
      "安全",
      "审核",
      "风控",
      "合规",
      "security",
      "audit",
      "risk",
      "compliance",
    ],
  },
  {
    icon: Scale,
    keywords: ["法律", "法务", "合同", "legal", "law", "contract"],
  },
  {
    icon: Globe2,
    keywords: [
      "翻译",
      "国际",
      "语言",
      "translate",
      "global",
      "language",
      "locale",
    ],
  },
  {
    icon: BriefcaseBusiness,
    keywords: [
      "运营",
      "项目",
      "商务",
      "管理",
      "operation",
      "project",
      "business",
      "manager",
    ],
  },
  {
    icon: Lightbulb,
    keywords: [
      "创意",
      "策划",
      "想法",
      "idea",
      "creative",
      "strategy",
      "planner",
    ],
  },
  {
    icon: BrainCircuit,
    keywords: ["智能", "顾问", "专家", "ai", "agent", "expert", "advisor"],
  },
];

const AGENT_FALLBACK_ICONS: LucideIcon[] = [
  Bot,
  Sparkles,
  Search,
  BrainCircuit,
  BriefcaseBusiness,
  FileSearch,
  BarChart3,
  Lightbulb,
  Code2,
  PenTool,
  Globe2,
  ShieldCheck,
  Palette,
  Megaphone,
  Headphones,
  Scale,
];

/**
 * Stable preset palette for the avatar picker. The agent's `avatar` column
 * stores the preset `key`; the icon is resolved from it. Keep keys stable —
 * renaming a key orphans every agent that picked it.
 */
export const AVATAR_PRESETS: Array<{ key: string; icon: LucideIcon }> = [
  { key: "bot", icon: Bot },
  { key: "sparkles", icon: Sparkles },
  { key: "brain", icon: BrainCircuit },
  { key: "code", icon: Code2 },
  { key: "analyst", icon: BarChart3 },
  { key: "research", icon: FileSearch },
  { key: "writer", icon: PenTool },
  { key: "design", icon: Palette },
  { key: "marketing", icon: Megaphone },
  { key: "support", icon: Headphones },
  { key: "security", icon: ShieldCheck },
  { key: "legal", icon: Scale },
  { key: "global", icon: Globe2 },
  { key: "business", icon: BriefcaseBusiness },
  { key: "idea", icon: Lightbulb },
  { key: "search", icon: Search },
];

const AVATAR_PRESET_MAP = new Map<string, LucideIcon>(
  AVATAR_PRESETS.map((p) => [p.key, p.icon]),
);

/** Resolve an explicit avatar preset key to its icon, or null when unset/unknown. */
export function getAvatarIcon(avatar?: string | null): LucideIcon | null {
  if (!avatar) return null;
  return AVATAR_PRESET_MAP.get(avatar) ?? null;
}

export function pickAgentIcon(
  agent: Agent,
  usedIcons = new Set<LucideIcon>(),
): LucideIcon {
  // An explicit avatar preset always wins over the keyword heuristic.
  const preset = getAvatarIcon(agent.avatar);
  if (preset) return preset;

  const haystack =
    `${agent.name} ${agent.slug} ${agent.description}`.toLocaleLowerCase();

  const matched = AGENT_ICON_KEYWORDS.find(
    ({ icon, keywords }) =>
      !usedIcons.has(icon) &&
      keywords.some((keyword) => haystack.includes(keyword)),
  )?.icon;
  if (matched) return matched;

  const hash = Array.from(agent.slug || agent.name).reduce(
    (sum, char) => sum + char.charCodeAt(0),
    0,
  );
  for (let i = 0; i < AGENT_FALLBACK_ICONS.length; i += 1) {
    const icon = AGENT_FALLBACK_ICONS[(hash + i) % AGENT_FALLBACK_ICONS.length];
    if (!usedIcons.has(icon)) return icon;
  }
  return Bot;
}

export function AgentIconGlyph({
  icon,
  className,
}: {
  icon: LucideIcon;
  className?: string;
}) {
  return createElement(icon, { className });
}
