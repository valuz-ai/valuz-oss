/**
 * Resolve a public asset path relative to the configured Vite base URL.
 *
 * In the webui we use `createBrowserRouter`, so a bare relative path such as
 * `./logo.png` is resolved against the current route (e.g. `/settings/logo.png`)
 * and returns 404. Desktop uses `createHashRouter` plus `base: "./"`, where the
 * same relative path works because the document URL never changes.
 *
 * Using `import.meta.env.BASE_URL` gives the correct root-relative (webui:
 * `/`) or relative (desktop: `./`) prefix for both targets.
 */
function getViteBase(): string {
  // Avoid depending on Vite's type declarations so this utility can live in
  // `@valuz/shared` without pulling in `vite/client`. When BASE_URL is not
  // injected (e.g. non-Vite consumers), fall back to "./" so behaviour
  // matches the legacy relative paths used before this helper existed.
  const viteEnv = (import.meta as unknown as { env?: Record<string, string> })
    .env;
  return viteEnv?.BASE_URL ?? "./";
}

export function assetUrl(path: string, base = getViteBase()): string {
  const cleanPath = path.replace(/^\.?\/+/, "");
  const baseWithSlash = base.endsWith("/") ? base : `${base}/`;
  return `${baseWithSlash}${cleanPath}`;
}
