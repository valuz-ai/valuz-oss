/**
 * Shared ``fetchJson`` factory — eliminates the 14 duplicate copies that
 * were scattered across the API modules.
 *
 * Each API module calls ``createFetchJson(() => _apiBase)`` once at
 * module scope and uses the returned function for all requests.
 * Call-sites stay ``fetchJson(path, init)`` — no changes needed.
 */

import { safeLocalGet } from "@valuz/shared";

/** Pull a user-facing message from FastAPI's ``{detail: ...}`` body. */
function extractErrorMessage(text: string): string | null {
  try {
    const parsed = JSON.parse(text);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail.reason === "string") return detail.reason;
  } catch {
    // not JSON
  }
  return null;
}

/** Read the current UI locale from localStorage. Mirrors
 * ``readStoredLocale`` in ``@valuz/shared/i18n``. Used to attach
 * ``Accept-Language`` to every backend request so server-side
 * resources (e.g. ``GET /v1/connectors/recommended``) can pick the
 * right copy from i18n-shaped catalog entries. */
function currentLocale(): "zh-CN" | "en-US" {
  const v = safeLocalGet("valuz-locale");
  return v === "en-US" || v === "zh-CN" ? v : "zh-CN";
}

export function createFetchJson(getBase: () => string) {
  return async function fetchJson<T>(
    path: string,
    init?: RequestInit,
  ): Promise<T> {
    // Merge Accept-Language into any caller-supplied headers without
    // clobbering them. Locale is read per-request, so locale flips
    // mid-session don't require a transport rebuild.
    const headers = new Headers(init?.headers);
    if (!headers.has("Accept-Language")) {
      headers.set("Accept-Language", currentLocale());
    }
    let res: Response;
    try {
      res = await fetch(`${getBase()}${path}`, { ...init, headers });
    } catch (err) {
      throw new Error(
        err instanceof TypeError
          ? "无法连接到后端服务，请检查应用进程是否运行"
          : err instanceof Error
            ? err.message
            : "网络请求失败",
      );
    }
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(
        extractErrorMessage(text) ?? `API ${res.status}: ${text}`,
      );
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  };
}
