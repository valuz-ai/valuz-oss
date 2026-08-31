import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  ChevronRight,
  Search,
  Sparkles,
} from "lucide-react";
import {
  marketplaceApi,
  useTranslation,
  type MarketplaceItem,
  type MarketplaceItemDetail,
  type MarketplaceItemType,
  type MarketplaceSubcategory,
} from "@valuz/core";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Skeleton,
} from "@valuz/ui";

import {
  MarketplaceSourcePill,
  marketplaceIcon,
  tintFor,
} from "./marketplace-ui";
import { resolveTemplateText, templateResources } from "../lib/template-library";

export type TemplateLibraryKind = "playbook" | "automation";

export interface TemplateLibraryProps {
  kind: TemplateLibraryKind;
  variant?: "full" | "recommended";
  onBrowseAll?: () => void;
  onUse: (detail: MarketplaceItemDetail) => void;
}

const PAGE_SIZE = 60;

function itemType(kind: TemplateLibraryKind): MarketplaceItemType {
  return kind === "playbook" ? "playbook_template" : "automation_template";
}

function TemplateIcon({ item }: { item: MarketplaceItem }) {
  const isImage = Boolean(item.icon && /^https?:\/\//.test(item.icon));
  const Icon = marketplaceIcon(item.icon);
  const tint = tintFor(item.id);
  return (
    <div
      className="flex h-10 w-10 flex-none items-center justify-center overflow-hidden rounded-[10px]"
      style={isImage ? undefined : { background: tint.bg, color: tint.fg }}
    >
      {isImage ? (
        <img src={item.icon ?? undefined} alt="" className="h-full w-full object-cover" />
      ) : (
        <Icon className="h-5 w-5" />
      )}
    </div>
  );
}

function TemplateCard({
  item,
  onOpen,
}: {
  item: MarketplaceItem;
  onOpen: (item: MarketplaceItem) => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      className="group flex min-h-[172px] w-full flex-col rounded-xl border border-surface-border bg-surface p-4 text-left transition hover:-translate-y-px hover:border-brand/35 hover:shadow-[var(--shadow-2)]"
    >
      <div className="flex items-start gap-3">
        <TemplateIcon item={item} />
        <div className="min-w-0 flex-1">
          <div className="line-clamp-1 text-sm font-semibold tracking-tight text-ink-heading group-hover:text-brand">
            {item.title}
          </div>
          <div className="mt-1.5 flex min-w-0 items-center gap-1.5">
            <MarketplaceSourcePill source={item.source} itemType={item.type} />
            {item.category_label ? (
              <span className="truncate text-2xs text-ink-meta">{item.category_label}</span>
            ) : null}
          </div>
        </div>
      </div>
      <p className="mt-3 line-clamp-2 text-xs leading-5 text-ink-body">
        {item.description}
      </p>
      <div className="mt-auto flex items-end justify-between gap-3 pt-3">
        <div className="flex min-w-0 flex-wrap gap-1">
          {(item.scenario_tags ?? []).slice(0, 2).map((tag) => (
            <Badge key={tag} variant="metaNeutral" className="max-w-[120px] truncate">
              {tag}
            </Badge>
          ))}
        </div>
        <span className="flex flex-none items-center gap-0.5 text-xs font-medium text-brand">
          {t("templateLibrary.details")}
          <ChevronRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </button>
  );
}

function DetailSection({ title, values }: { title: string; values?: string[] | null }) {
  if (!values?.length) return null;
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold text-ink-heading">{title}</h3>
      <ol className="space-y-1.5 text-xs leading-5 text-ink-body">
        {values.map((value, index) => (
          <li key={`${index}-${value}`} className="flex gap-2">
            <span className="font-mono text-ink-meta">{index + 1}.</span>
            <span>{value}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function resourceLabel(resource: unknown, locale: string): string {
  if (typeof resource === "string") return resource;
  if (!resource || typeof resource !== "object") return "";
  const row = resource as Record<string, unknown>;
  return (
    resolveTemplateText(row.name ?? row.label, locale) ||
    (typeof row.slug === "string" ? row.slug : "") ||
    (typeof row.type === "string" ? row.type : "")
  );
}

export function TemplateLibrary({
  kind,
  variant = "full",
  onBrowseAll,
  onUse,
}: TemplateLibraryProps) {
  const { t, locale } = useTranslation();
  const [scenarios, setScenarios] = useState<MarketplaceSubcategory[]>([]);
  const [scenario, setScenario] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<MarketplaceItemDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const requestId = useRef(0);

  useEffect(() => {
    let cancelled = false;
    marketplaceApi
      .categories(kind)
      .then((result) => {
        if (cancelled) return;
        setScenarios(result.scenario_tags ?? []);
      })
      .catch(() => {
        if (!cancelled) {
          setScenarios([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [kind]);

  useEffect(() => {
    const id = (requestId.current += 1);
    setLoading(true);
    setFailed(false);
    marketplaceApi
      .list({
        type: itemType(kind),
        scenario: variant === "full" ? scenario ?? undefined : undefined,
        q: variant === "full" && query.trim() ? query.trim() : undefined,
        page: 1,
        page_size: variant === "recommended" ? 6 : PAGE_SIZE,
      })
      .then((result) => {
        if (requestId.current === id) setItems(result.items);
      })
      .catch(() => {
        if (requestId.current === id) {
          setItems([]);
          setFailed(true);
        }
      })
      .finally(() => {
        if (requestId.current === id) setLoading(false);
      });
  }, [kind, query, scenario, variant]);

  const scenarioLabels = useMemo(
    () => new Map(scenarios.map((entry) => [entry.key, entry.label])),
    [scenarios],
  );
  const displayItems = useMemo(
    () =>
      items.map((item) => ({
        ...item,
        scenario_tags: (item.scenario_tags ?? []).map(
          (tag) => scenarioLabels.get(tag) ?? tag,
        ),
      })),
    [items, scenarioLabels],
  );

  const openDetail = useCallback((item: MarketplaceItem) => {
    setDetailLoading(true);
    marketplaceApi
      .get(item.id)
      .then(setSelected)
      .catch(() => setSelected(null))
      .finally(() => setDetailLoading(false));
  }, []);

  const title =
    kind === "playbook"
      ? t("templateLibrary.playbookRecommended")
      : t("templateLibrary.automationRecommended");
  const resources = selected ? templateResources(selected) : [];
  const content = (
    <div className={variant === "recommended" ? "w-full" : "min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4"}>
      {variant === "recommended" ? (
        <div className="mb-3 flex items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-heading">
              <Sparkles className="h-4 w-4 text-brand" />
              {title}
            </div>
            <p className="mt-1 text-xs text-ink-meta">{t("templateLibrary.recommendedHint")}</p>
          </div>
          {onBrowseAll ? (
            <Button variant="ghost" size="sm" onClick={onBrowseAll}>
              {t("templateLibrary.browseAll")}
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          ) : null}
        </div>
      ) : (
        <div className="mb-4 space-y-3">
          <div className="relative max-w-md">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-meta" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("templateLibrary.search")}
              className="pl-9"
            />
          </div>
          {scenarios.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              <Button
                size="sm"
                variant={scenario === null ? "default" : "ghost"}
                onClick={() => setScenario(null)}
              >
                {t("templateLibrary.allScenarios")}
              </Button>
              {scenarios.map((entry) => (
                <Button
                  key={entry.key}
                  size="sm"
                  variant={scenario === entry.key ? "default" : "ghost"}
                  onClick={() => setScenario(entry.key)}
                >
                  {entry.label}
                  {entry.count != null ? (
                    <span className="text-2xs text-ink-meta">{entry.count}</span>
                  ) : null}
                </Button>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
          {Array.from({ length: variant === "recommended" ? 3 : 6 }).map((_, index) => (
            <Skeleton key={index} className="h-[172px] rounded-xl" />
          ))}
        </div>
      ) : failed ? (
        <div className="rounded-xl border border-dashed border-surface-border p-8 text-center text-sm text-ink-meta">
          {t("templateLibrary.loadFailed")}
        </div>
      ) : displayItems.length === 0 ? (
        <div className="rounded-xl border border-dashed border-surface-border p-8 text-center text-sm text-ink-meta">
          {t("templateLibrary.noResults")}
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
          {displayItems.map((item) => (
            <TemplateCard key={item.id} item={item} onOpen={openDetail} />
          ))}
        </div>
      )}
    </div>
  );

  return (
    <>
      {content}

      <Dialog
        open={selected !== null || detailLoading}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          {selected ? (
            <>
              <DialogHeader>
                <div className="flex items-start gap-3">
                  <TemplateIcon item={selected} />
                  <div className="min-w-0">
                    <DialogTitle>{selected.title}</DialogTitle>
                    <DialogDescription className="mt-1.5 leading-5">
                      {selected.description}
                    </DialogDescription>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <MarketplaceSourcePill source={selected.source} itemType={selected.type} />
                      {selected.category_label ? (
                        <Badge variant="metaNeutral">{selected.category_label}</Badge>
                      ) : null}
                    </div>
                  </div>
                </div>
              </DialogHeader>
              <div className="space-y-5 py-2">
                <DetailSection title={t("templateLibrary.workflow")} values={selected.workflow} />
                <DetailSection title={t("templateLibrary.deliverables")} values={selected.deliverables} />
                <DetailSection title={t("templateLibrary.usageNotes")} values={selected.usage_notes} />
                {resources.length > 0 ? (
                  <section>
                    <h3 className="mb-2 text-xs font-semibold text-ink-heading">
                      {t("templateLibrary.resources")}
                    </h3>
                    <div className="flex flex-wrap gap-1.5">
                      {resources.map((resource, index) => {
                        const label = resourceLabel(resource, locale);
                        return label ? (
                          <Badge key={`${index}-${label}`} variant="metaNeutral">
                            {label}
                          </Badge>
                        ) : null;
                      })}
                    </div>
                  </section>
                ) : null}
              </div>
              <DialogFooter>
                <Button
                  onClick={() => {
                    onUse(selected);
                    setSelected(null);
                  }}
                >
                  {kind === "playbook"
                    ? t("templateLibrary.usePlaybook")
                    : t("templateLibrary.useAutomation")}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader className="sr-only">
                <DialogTitle>{t("templateLibrary.loadingDetails")}</DialogTitle>
                <DialogDescription>
                  {t("templateLibrary.loadingDetailsHint")}
                </DialogDescription>
              </DialogHeader>
              <div className="flex min-h-48 items-center justify-center">
                <Bot className="h-5 w-5 animate-pulse text-ink-meta" />
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
