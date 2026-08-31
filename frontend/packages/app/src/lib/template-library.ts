import type {
  ActionKind,
  MarketplaceItem,
  MarketplaceItemDetail,
  PlaybookStatus,
  Trigger,
} from "@valuz/core";

export const RECOMMENDED_TEMPLATE_LIMIT = 8;

const FINANCE_RECOMMENDATION_ORDER = {
  playbook: [
    "general-finance",
    "macro-strategy",
    "brokerage",
    "market-data",
    "portfolio-risk",
    "equity-research",
    "quant-trading",
    "valuation-modeling",
  ],
  automation: [
    "brokerage",
    "market-data",
    "portfolio-risk",
    "general-finance",
    "macro-strategy",
    "equity-research",
    "quant-trading",
    "valuation-modeling",
  ],
} as const;

export interface PlaybookTemplatePrefill {
  name: string;
  content: string;
  status: PlaybookStatus;
  default_agent_slug?: string;
}

export interface AutomationTemplatePrefill {
  name: string;
  prompt_template: string;
  agent_slug: string;
  trigger: Trigger;
  action_kind: ActionKind;
  worktree: boolean;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function resolveTemplateText(value: unknown, locale: string): string {
  if (typeof value === "string") return value;
  const values = record(value);
  const exact = values[locale];
  if (typeof exact === "string") return exact;
  const language = locale.split("-")[0];
  const languageMatch = Object.entries(values).find(
    ([key, entry]) => key.split("-")[0] === language && typeof entry === "string",
  )?.[1];
  if (typeof languageMatch === "string") return languageMatch;
  const english = values["en-US"];
  if (typeof english === "string") return english;
  return Object.values(values).find((entry): entry is string => typeof entry === "string") ?? "";
}

export function playbookTemplatePrefill(
  detail: MarketplaceItemDetail,
  locale: string,
): PlaybookTemplatePrefill {
  const manifest = record(detail.install_manifest);
  const rawStatus = manifest.status;
  const status: PlaybookStatus =
    rawStatus === "active" || rawStatus === "retired" ? rawStatus : "draft";
  const defaultAgent = manifest.default_agent_slug;
  return {
    name: detail.title,
    content: resolveTemplateText(manifest.content, locale),
    status,
    ...(typeof defaultAgent === "string" && defaultAgent
      ? { default_agent_slug: defaultAgent }
      : {}),
  };
}

function parseTrigger(value: unknown): Trigger {
  const trigger = record(value);
  if (trigger.kind === "interval") {
    return {
      kind: "interval",
      seconds:
        typeof trigger.seconds === "number" && trigger.seconds > 0
          ? trigger.seconds
          : 300,
    };
  }
  if (trigger.kind === "manual") return { kind: "manual" };
  return {
    kind: "cron",
    cron_expr:
      typeof trigger.cron_expr === "string" && trigger.cron_expr
        ? trigger.cron_expr
        : "0 9 * * *",
    timezone:
      typeof trigger.timezone === "string" && trigger.timezone
        ? trigger.timezone
        : "Asia/Shanghai",
  };
}

export function automationTemplatePrefill(
  detail: MarketplaceItemDetail,
  locale: string,
): AutomationTemplatePrefill {
  const manifest = record(detail.install_manifest);
  return {
    name: detail.title,
    prompt_template: resolveTemplateText(manifest.prompt_template, locale),
    agent_slug:
      typeof manifest.default_agent_slug === "string"
        ? manifest.default_agent_slug
        : "",
    trigger: parseTrigger(manifest.trigger),
    action_kind: manifest.action_kind === "task" ? "task" : "chat",
    worktree: manifest.worktree === true,
  };
}

export function templateResources(detail: MarketplaceItemDetail): unknown[] {
  const resources = record(detail.install_manifest).resources;
  return Array.isArray(resources) ? resources : [];
}

/**
 * Keep empty-state recommendations compact while curating the Finance edition
 * around its highest-frequency jobs. Full template-library views never use
 * this projection and continue to show the complete catalog.
 */
export function recommendedTemplates(
  items: MarketplaceItem[],
  kind: "playbook" | "automation",
  limit = RECOMMENDED_TEMPLATE_LIMIT,
): MarketplaceItem[] {
  const boundedLimit = Math.max(0, limit);
  const isFinanceCatalog =
    items.length > 0 && items.every((item) => item.category === "finance");
  if (!isFinanceCatalog) return items.slice(0, boundedLimit);

  const order: readonly string[] = FINANCE_RECOMMENDATION_ORDER[kind];
  const rank = (item: MarketplaceItem): number => {
    const ranks = item.subcategories
      .map((subcategory) => order.indexOf(subcategory))
      .filter((value) => value >= 0);
    return ranks.length > 0 ? Math.min(...ranks) : order.length;
  };

  return items
    .map((item, index) => ({ item, index, rank: rank(item) }))
    .sort((left, right) => left.rank - right.rank || left.index - right.index)
    .slice(0, boundedLimit)
    .map(({ item }) => item);
}
