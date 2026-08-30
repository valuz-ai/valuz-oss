import { useCallback, useEffect, useState } from "react";
import { marketplaceApi } from "@valuz/core";
import type { MarketplaceCategory } from "@valuz/core";

type MarketplaceCategoryKind = "skill" | "agent" | "connector" | "plugin";

export function useMarketplaceCategoryFilter(kind: MarketplaceCategoryKind) {
  const [categories, setCategories] = useState<MarketplaceCategory[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [subcategory, setSubcategory] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    marketplaceApi
      .categories(kind)
      .then((response) => {
        if (cancelled) return;
        setCategories(response.categories);
        const financeOnly =
          response.categories.length === 1 &&
          response.categories[0]?.key === "finance" &&
          (response.categories[0].subcategories?.length ?? 0) > 0;
        if (financeOnly) {
          // Finance editions intentionally expose only the Finance primary
          // category. Select it automatically so the useful second level is
          // visible immediately instead of hiding behind a redundant click.
          setCategory((current) => current ?? "finance");
        }
      })
      .catch(() => {
        if (!cancelled) setCategories([]);
      });
    return () => {
      cancelled = true;
    };
  }, [kind]);

  const selectCategory = useCallback((next: string | null) => {
    setCategory(next);
    setSubcategory(null);
  }, []);

  const reset = useCallback(() => {
    setCategory(null);
    setSubcategory(null);
  }, []);

  return {
    categories,
    category,
    subcategory,
    selectCategory,
    selectSubcategory: setSubcategory,
    reset,
  };
}
