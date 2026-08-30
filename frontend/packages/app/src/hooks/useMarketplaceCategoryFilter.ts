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
