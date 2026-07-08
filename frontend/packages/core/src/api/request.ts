import { safeLocalGet } from "@valuz/shared";

type CacheTag = string;

export interface RequestCacheOptions {
  ttlMs: number;
  tags?: CacheTag[];
  key?: string;
}

export type RequestOptions = Omit<RequestInit, "cache"> & {
  baseUrl?: string;
  json?: unknown;
  cache?: RequestCache | RequestCacheOptions;
  timeoutMs?: number;
};

export interface RequestCacheInvalidation {
  keys?: string[];
  tags?: CacheTag[];
}

interface ExtractedError {
  message: string | null;
  i18nKey?: string;
  i18nParams?: Record<string, unknown>;
}

interface CacheEntry {
  expiresAt: number;
  tags: Set<CacheTag>;
  value?: unknown;
  promise?: Promise<unknown>;
}

const DEFAULT_API_BASE =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

const responseCache = new Map<string, CacheEntry>();

/**
 * Error thrown for any non-2xx response. Carries the HTTP ``status`` so callers
 * can branch on it (e.g. ``402`` -> insufficient balance) instead of string-
 * matching the message. ``message`` is the user-facing detail extracted from
 * the FastAPI ``{detail}`` body; ``body`` keeps the raw response text.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body?: string;
  readonly i18nKey?: string;
  readonly i18nParams?: Record<string, unknown>;

  constructor(
    message: string,
    status: number,
    body?: string,
    i18nKey?: string,
    i18nParams?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.i18nKey = i18nKey;
    this.i18nParams = i18nParams;
  }
}

export function buildApiUrl(baseUrl: string, path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const base = baseUrl.replace(/\/+$/, "");
  const suffix = path.replace(/^\/+/, "");
  return `${base}/${suffix}`;
}

export function clearRequestCacheForTests(): void {
  responseCache.clear();
}

export function invalidateRequestCache(
  invalidation: RequestCacheInvalidation = {},
): void {
  const keys = new Set(invalidation.keys ?? []);
  const tags = new Set(invalidation.tags ?? []);
  if (keys.size === 0 && tags.size === 0) {
    responseCache.clear();
    return;
  }

  for (const [key, entry] of responseCache) {
    if (keys.has(key) || [...tags].some((tag) => entry.tags.has(tag))) {
      responseCache.delete(key);
    }
  }
}

export async function requestJson<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const prepared = prepareRequest(path, options);
  const cacheKey = getCacheKey(prepared);
  if (!cacheKey) return parseJsonResponse<T>(await fetchWithHandling(prepared));

  const now = Date.now();
  const cached = responseCache.get(cacheKey);
  if (cached && cached.expiresAt > now) {
    if (cached.promise) return cached.promise as Promise<T>;
    return cached.value as T;
  }

  const promise = fetchWithHandling(prepared)
    .then((response) => parseJsonResponse<T>(response))
    .then((value) => {
      responseCache.set(cacheKey, {
        expiresAt: Date.now() + (prepared.cache?.ttlMs ?? 0),
        tags: new Set(prepared.cache?.tags ?? []),
        value,
      });
      return value;
    })
    .catch((error) => {
      responseCache.delete(cacheKey);
      throw error;
    });

  responseCache.set(cacheKey, {
    expiresAt: now + (prepared.cache?.ttlMs ?? 0),
    tags: new Set(prepared.cache?.tags ?? []),
    promise,
  });
  return promise;
}

export async function requestRaw(
  path: string,
  options: RequestOptions = {},
): Promise<Response> {
  return fetchWithHandling(prepareRequest(path, options));
}

export async function requestBlob(
  path: string,
  options: RequestOptions = {},
): Promise<{ blob: Blob; headers: Headers }> {
  const response = await requestRaw(path, options);
  return { blob: await response.blob(), headers: response.headers };
}

function currentLocale(): "zh-CN" | "en-US" {
  const value = safeLocalGet("valuz-locale");
  return value === "en-US" || value === "zh-CN" ? value : "zh-CN";
}

function extractError(text: string): ExtractedError {
  try {
    const parsed = JSON.parse(text);
    const detail = parsed?.detail;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object") {
      const i18nKey =
        typeof detail.message_key === "string" ? detail.message_key : undefined;
      const i18nParams =
        detail.message_params && typeof detail.message_params === "object"
          ? (detail.message_params as Record<string, unknown>)
          : undefined;
      const message =
        typeof detail.message === "string"
          ? detail.message
          : typeof detail.reason === "string"
            ? detail.reason
            : null;
      return { message, i18nKey, i18nParams };
    }
  } catch {
    // Body is not JSON; fall through to the generic message.
  }
  return { message: null };
}

function prepareRequest(path: string, options: RequestOptions): {
  url: string;
  init: RequestInit;
  method: string;
  cache?: RequestCacheOptions;
  timeoutMs?: number;
} {
  const {
    baseUrl = DEFAULT_API_BASE,
    json,
    cache,
    timeoutMs,
    headers: callerHeaders,
    ...init
  } = options;
  const headers = new Headers(callerHeaders);
  if (!headers.has("Accept-Language")) {
    headers.set("Accept-Language", currentLocale());
  }

  let body = init.body;
  if (json !== undefined) {
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    body = JSON.stringify(json);
  }

  const method = (init.method ?? "GET").toUpperCase();
  const requestCache = isRequestCacheOptions(cache) ? cache : undefined;
  return {
    url: buildApiUrl(baseUrl, path),
    init: {
      ...init,
      method,
      headers,
      body,
      ...(typeof cache === "string" ? { cache } : {}),
    },
    method,
    cache: requestCache,
    timeoutMs,
  };
}

function isRequestCacheOptions(
  cache: RequestCache | RequestCacheOptions | undefined,
): cache is RequestCacheOptions {
  return (
    typeof cache === "object" &&
    cache !== null &&
    typeof cache.ttlMs === "number"
  );
}

function getCacheKey(prepared: {
  url: string;
  init: RequestInit;
  method: string;
  cache?: RequestCacheOptions;
}): string | null {
  if (!prepared.cache || prepared.cache.ttlMs <= 0 || prepared.method !== "GET") {
    return null;
  }
  if (prepared.cache.key) return prepared.cache.key;
  const locale = new Headers(prepared.init.headers).get("Accept-Language") ?? "";
  return `${prepared.method} ${prepared.url} ${locale}`;
}

async function fetchWithHandling(prepared: {
  url: string;
  init: RequestInit;
  timeoutMs?: number;
}): Promise<Response> {
  let timedOut = false;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const callerSignal = prepared.init.signal;
  const controller =
    prepared.timeoutMs || callerSignal ? new AbortController() : undefined;

  const abortFromCaller = (): void => controller?.abort(callerSignal?.reason);
  if (callerSignal) {
    if (callerSignal.aborted) abortFromCaller();
    else callerSignal.addEventListener("abort", abortFromCaller, { once: true });
  }
  if (controller && prepared.timeoutMs) {
    timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, prepared.timeoutMs);
  }

  let response: Response;
  try {
    response = await fetch(prepared.url, {
      ...prepared.init,
      signal: controller?.signal ?? callerSignal,
    });
  } catch (err) {
    if (timedOut) {
      throw new Error("请求超时，请稍后重试");
    }
    throw new Error(
      err instanceof TypeError
        ? "无法连接到后端服务，请检查应用进程是否运行"
        : err instanceof Error
          ? err.message
          : "网络请求失败",
    );
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }

  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText);
    const { message, i18nKey, i18nParams } = extractError(text);
    throw new ApiError(
      message ?? `API ${response.status}: ${text}`,
      response.status,
      text,
      i18nKey,
      i18nParams,
    );
  }
  return response;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
