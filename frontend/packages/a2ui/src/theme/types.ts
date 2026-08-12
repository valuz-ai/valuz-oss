export type A2UIThemeMode = "light" | "dark";
export type A2UIThemeTokens = Record<`--va2-${string}`, string>;

export interface A2UIThemeExtension {
  /** Stable distribution or product id, for example `finance`. */
  id: string;
  /** `default` is the package-owned base theme. Other parents must be registered. */
  extends?: readonly string[];
  /** New tokens must be namespaced as `--va2-<id>-*`. */
  tokens?: Partial<Record<A2UIThemeMode, Partial<A2UIThemeTokens>>>;
  /** Deliberate replacements for existing base or parent tokens. */
  overrides?: Partial<Record<A2UIThemeMode, Partial<A2UIThemeTokens>>>;
  /** Named visualization grammar implemented by the renderer and CSS theme. */
  visualizationPreset?: string;
}
