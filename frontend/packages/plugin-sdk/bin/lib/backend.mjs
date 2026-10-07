// The local Valuz backend, for ``valuz-plugin dev``: dev-link the plugin
// directory once, then reload it after every rebuild (task card 04 §D:
// POST /v1/app-plugins/dev-link {path}, POST …/{id}/reload).
//
// Base URL: --backend, else $VALUZ_BACKEND_BASE_URL, else http://127.0.0.1:8000
// (the Go CLI's convention). A bearer token is sent when --token or
// $VALUZ_BACKEND_TOKEN is set.
/* global fetch, AbortSignal -- Node 18+ globals */
import process from "node:process";

export const DEFAULT_BACKEND = "http://127.0.0.1:8000";
const APP_PLUGINS_API = "/v1/app-plugins";

export function resolveBackend(flag, env = process.env) {
  const base = flag || env.VALUZ_BACKEND_BASE_URL || DEFAULT_BACKEND;
  return base.replace(/\/+$/, "");
}

export class BackendError extends Error {
  constructor(message, { status = null, code = null, unreachable = false } = {}) {
    super(message);
    this.name = "BackendError";
    this.status = status;
    this.code = code;
    this.unreachable = unreachable;
  }
}

async function call(base, method, apiPath, { body, token, timeoutMs = 15000 } = {}) {
  const headers = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (token) headers.authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`${base}${apiPath}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    throw new BackendError(`cannot reach the Valuz backend at ${base} (${error.cause?.code ?? error.message})`, {
      unreachable: true,
    });
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data?.detail ?? data;
    const code = (typeof detail === "object" && detail?.code) || data?.code || null;
    const message =
      (typeof detail === "object" && (detail?.message ?? (Array.isArray(detail?.errors) ? detail.errors.join("; ") : null))) ||
      (typeof detail === "string" ? detail : null) ||
      text.slice(0, 300) ||
      response.statusText;
    throw new BackendError(`${method} ${apiPath} → ${response.status}: ${message}`, {
      status: response.status,
      code,
    });
  }
  return data;
}

/** POST /v1/app-plugins/dev-link → ``{ plugin }``. */
export function devLink(base, absPath, options = {}) {
  return call(base, "POST", `${APP_PLUGINS_API}/dev-link`, { ...options, body: { path: absPath } });
}

/** POST /v1/app-plugins/{id}/reload → ``{ plugin }``. */
export function reloadPlugin(base, id, options = {}) {
  return call(base, "POST", `${APP_PLUGINS_API}/${encodeURIComponent(id)}/reload`, options);
}
