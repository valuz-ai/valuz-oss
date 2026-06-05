import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { safeLocalGet } from "@valuz/shared";
import { t } from "@valuz/shared/i18n";

const DEFAULT_SHORTCUTS: Record<string, string> = {
  "new-chat": "Meta+n",
  "command-palette": "Meta+k",
  send: "Meta+Enter",
  upload: "Meta+u",
  settings: "Meta+,",
};

function normalizeKey(raw: string): string {
  const map: Record<string, string> = {
    "⌘": "Meta",
    "⇧": "Shift",
    "⌥": "Alt",
    "⏎": "Enter",
  };
  return map[raw] ?? raw;
}

function parseDisplayCombo(display: string): { meta: boolean; key: string } {
  const chars = [...display];
  let meta = false;
  let key = "";
  for (const ch of chars) {
    const n = normalizeKey(ch);
    if (n === "Meta") {
      meta = true;
    } else {
      key = n;
    }
  }
  return { meta, key };
}

function getShortcuts(): Record<string, string> {
  try {
    const stored = JSON.parse(safeLocalGet("valuz-shortcuts") ?? "{}");
    return { ...DEFAULT_SHORTCUTS, ...stored };
  } catch {
    return { ...DEFAULT_SHORTCUTS };
  }
}

export const useGlobalShortcuts = () => {
  const navigate = useNavigate();

  useEffect(() => {
    const shortcuts = getShortcuts();

    const comboMap = new Map<string, string>();
    for (const [id, combo] of Object.entries(shortcuts)) {
      const display = combo || DEFAULT_SHORTCUTS[id];
      const parsed = parseDisplayCombo(display);
      const normalized = `${parsed.meta ? "meta+" : ""}${parsed.key.toLowerCase()}`;
      comboMap.set(normalized, id);
    }

    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if ((e.target as HTMLElement)?.isContentEditable) return;

      const parts: string[] = [];
      if (e.metaKey || e.ctrlKey) parts.push("meta");
      const key = e.key;
      parts.push(key.toLowerCase());
      const normalized = parts.join("+");

      const actionId = comboMap.get(normalized);
      if (!actionId) return;

      switch (actionId) {
        case "new-chat":
          e.preventDefault();
          navigate("/conversation/new");
          break;
        case "settings":
          e.preventDefault();
          navigate("/settings");
          break;
        case "command-palette":
          e.preventDefault();
          toast.info(
            t("commandPalette.placeholder" as Parameters<typeof t>[0]),
          );
          break;
        case "send":
        case "upload":
          e.preventDefault();
          document.dispatchEvent(
            new CustomEvent("valuz-shortcut", { detail: actionId }),
          );
          break;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigate]);
};
