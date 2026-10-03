import { t as _t } from "@valuz/shared/i18n";

/**
 * Boot-time failure screen for the OSS shells: shown when a REQUIRED OSS plugin
 * (``oss-core`` / ``oss-agents``) failed to load. Mounting the app without
 * them would show an empty shell, so the host says what failed instead.
 *
 * Plain DOM with no styling dependencies: it runs before React mounts.
 */
export function renderOssBootFailure(
  container: HTMLElement,
  error: unknown,
  reload: () => void = () => window.location.reload(),
): void {
  console.error("[boot] a required OSS plugin failed to load", error);

  const doc = container.ownerDocument;
  const panel = doc.createElement("div");
  panel.setAttribute("role", "alert");
  panel.dataset.slot = "boot-failure";
  panel.style.cssText =
    "max-width:640px;margin:64px auto;padding:0 24px;font-family:system-ui,sans-serif;";

  const title = doc.createElement("h1");
  title.style.cssText = "font-size:18px;font-weight:600;margin:0 0 12px;";
  title.textContent = _t("startup.error");

  const detail = doc.createElement("pre");
  detail.style.cssText =
    "white-space:pre-wrap;word-break:break-word;font-size:12px;opacity:0.75;margin:0 0 16px;";
  detail.textContent = error instanceof Error ? error.message : String(error);

  const retry = doc.createElement("button");
  retry.type = "button";
  retry.style.cssText =
    "padding:6px 14px;border:1px solid currentColor;border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer;";
  retry.textContent = _t("common.retry");
  retry.addEventListener("click", () => reload());

  panel.append(title, detail, retry);
  container.replaceChildren(panel);
}
