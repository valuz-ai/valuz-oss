/**
 * Shared marketplace presentation atoms — icon/tint/badge mapping used by
 * both the browse page and the import-preview dialog. The named icons and
 * accent tints follow the Marketplace design prototype (claude.ai/design →
 * Marketplace.dc.html).
 */

import type { LucideIcon } from "lucide-react";
import {
  BookOpen,
  Cloud,
  FileText,
  Gem,
  LineChart,
  Mic,
  PenLine,
  Plug,
  Puzzle,
  Search,
  Sparkles,
  Table2,
  Users,
} from "lucide-react";
import type { MarketplaceBadge, MarketplaceSource } from "@valuz/core";
import { useTranslation } from "@valuz/core";
import { Badge } from "@valuz/ui";

const NAMED_ICONS: Record<string, LucideIcon> = {
  chart: LineChart,
  search: Search,
  pen: PenLine,
  mic: Mic,
  cloud: Cloud,
  table: Table2,
  doc: FileText,
  book: BookOpen,
  gem: Gem,
  users: Users,
  sparkles: Sparkles,
  plug: Plug,
  puzzle: Puzzle,
};

export function marketplaceIcon(name?: string | null): LucideIcon {
  return (name && NAMED_ICONS[name]) || Sparkles;
}

/** Accent tint pairs from the design prototype's TINT palette. */
const TINTS: { bg: string; fg: string }[] = [
  { bg: "#ede9ff", fg: "#5b4be0" },
  { bg: "#e0f2fe", fg: "#0284c7" },
  { bg: "#ccfbf1", fg: "#0d9488" },
  { bg: "#fef3c7", fg: "#b45309" },
  { bg: "#fce7f3", fg: "#be185d" },
  { bg: "#e9eef5", fg: "#475569" },
];

export function tintFor(key: string): { bg: string; fg: string } {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) | 0;
  return TINTS[Math.abs(hash) % TINTS.length];
}

const BADGE_STYLES: Record<MarketplaceBadge, { bg: string; fg: string }> = {
  free_install: { bg: "var(--surface-soft)", fg: "var(--ink-meta)" },
  requires_api_key: { bg: "#fef3c7", fg: "#92400e" },
  third_party_cost: { bg: "#ffe4e6", fg: "#9f1239" },
  reviewed_skillhub: { bg: "var(--surface-soft)", fg: "var(--ink-meta)" },
  reviewed_valuz: { bg: "var(--surface-soft)", fg: "var(--ink-meta)" },
  community: { bg: "var(--surface-soft)", fg: "var(--ink-meta)" },
  verified: { bg: "var(--surface-soft)", fg: "var(--ink-meta)" },
  locked: { bg: "#fef3c7", fg: "#92400e" },
};

const BADGE_LABEL_KEYS: Record<MarketplaceBadge, string> = {
  free_install: "marketplace.badgeFreeInstall",
  requires_api_key: "marketplace.badgeRequiresApiKey",
  third_party_cost: "marketplace.badgeThirdPartyCost",
  reviewed_skillhub: "marketplace.badgeReviewedSkillhub",
  reviewed_valuz: "marketplace.badgeReviewedValuz",
  community: "marketplace.badgeCommunity",
  verified: "marketplace.badgeVerified",
  locked: "marketplace.badgeLocked",
};

export function MarketplaceBadgePill({ badge }: { badge: MarketplaceBadge }) {
  const { t } = useTranslation();
  const style = BADGE_STYLES[badge];
  return (
    <span
      className="inline-flex items-center rounded border border-surface-border px-1.5 py-0.5 text-[10px] font-medium"
      style={{ background: style.bg, color: style.fg }}
    >
      {t(BADGE_LABEL_KEYS[badge] as Parameters<typeof t>[0])}
    </span>
  );
}

const SOURCE_LABEL_KEYS: Record<MarketplaceSource, string> = {
  skillhub: "marketplace.sourceSkillhub",
  valuz_official: "marketplace.sourceValuzOfficial",
  modelscope: "marketplace.sourceModelScope",
  redskill: "marketplace.sourceRedskill",
  pluginmarket: "marketplace.sourcePluginMarket",
};

// One neutral look for every source — per-source colors made the cards read
// inconsistently across tabs. Uses the Badge primitive (metaNeutral) so the
// pill follows the design-system sizing/rounding/background tokens.
export function MarketplaceSourcePill({ source }: { source: MarketplaceSource }) {
  const { t } = useTranslation();
  return (
    <Badge variant="metaNeutral">
      {t(SOURCE_LABEL_KEYS[source] as Parameters<typeof t>[0])}
    </Badge>
  );
}

/** 18234 → "18.2k" — compact counter for downloads/stars. */
export function formatCount(n?: number | null): string {
  if (n == null) return "–";
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}

/** Bytes → human size for the pre-install file list. */
export function formatSize(bytes?: number | null): string {
  if (bytes == null) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}
