/**
 * Safe wrappers around `localStorage` / `sessionStorage`.
 *
 * jsdom's default test environment leaves `localStorage` defined as a plain
 * object without the Storage prototype methods on it, so a direct
 * `localStorage.getItem("x")` call throws
 *   TypeError: localStorage.getItem is not a function
 * Production Safari/Chrome private-mode + iframe-with-storage-blocked also
 * throw DOMException quotas on getItem.
 *
 * Use these helpers everywhere we touch the Web Storage API. They:
 *   - tolerate `localStorage`/`sessionStorage` being undefined (SSR/Node)
 *   - tolerate the methods being missing (jsdom default)
 *   - swallow synchronous throw (private mode, blocked cookies, etc.)
 *   - return `null` (read) / `false` (write) instead of throwing
 *
 * Keep these tiny and dep-free — they sit in @valuz/shared so every package
 * can use them without pulling in core's transport / store baggage.
 */

export function safeLocalGet(key: string): string | null {
  try {
    if (typeof localStorage === "undefined") return null;
    if (typeof localStorage.getItem !== "function") return null;
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function safeLocalSet(key: string, value: string): boolean {
  try {
    if (typeof localStorage === "undefined") return false;
    if (typeof localStorage.setItem !== "function") return false;
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

export function safeLocalRemove(key: string): boolean {
  try {
    if (typeof localStorage === "undefined") return false;
    if (typeof localStorage.removeItem !== "function") return false;
    localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export function safeSessionGet(key: string): string | null {
  try {
    if (typeof sessionStorage === "undefined") return null;
    if (typeof sessionStorage.getItem !== "function") return null;
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function safeSessionSet(key: string, value: string): boolean {
  try {
    if (typeof sessionStorage === "undefined") return false;
    if (typeof sessionStorage.setItem !== "function") return false;
    sessionStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}
