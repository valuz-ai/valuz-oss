export interface ResourceCategory<T> {
  /** Unique category ID (e.g. "official", "custom", "builtin") */
  id: string;
  /** Display label (i18n key or plain string) */
  label: string;
  /** Optional icon rendered before the label (ReactNode at usage sites) */
  icon?: unknown;
  /** Display order — lower values appear first */
  order: number;
  /** Predicate to test if an item belongs to this category */
  filter: (item: T) => boolean;
  /** Optional per-category sort comparator */
  sort?: (a: T, b: T) => number;
  /** Whether this category's items are collapsed by default */
  defaultCollapsed?: boolean;
  /** If true, items matched by this category are NOT removed from the
   *  assignment pool — they can also appear in later categories. */
  multiAssign?: boolean;
}
