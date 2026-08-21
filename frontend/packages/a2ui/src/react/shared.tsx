/* Shared renderer helpers and icon components intentionally live together below the app layer. */
/* eslint-disable react-refresh/only-export-components */
import type { ComponentContext } from "@a2ui/web_core/v0_9";
import {
  ArrowRight,
  Bot,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  Circle,
  FileText,
  HelpCircle,
  Image as ImageIcon,
  Info,
  Lightbulb,
  Link,
  Search,
  Sparkles,
  Star,
  TrendingUp,
  TriangleAlert,
  User,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Fragment, type CSSProperties, type ReactNode } from "react";

export type BuildChild = (id: string, basePath?: string) => ReactNode;

type ResolvedChild = string | { id: string; basePath: string };

export function RenderChildren({
  children,
  buildChild,
}: {
  children: unknown;
  buildChild: BuildChild;
}) {
  if (!Array.isArray(children)) return null;
  return children.map((child: ResolvedChild, index) => {
    if (typeof child === "string") {
      return <Fragment key={`${child}-${index}`}>{buildChild(child)}</Fragment>;
    }
    if (!child || typeof child.id !== "string") return null;
    return (
      <Fragment key={`${child.id}-${child.basePath}-${index}`}>
        {buildChild(child.id, child.basePath)}
      </Fragment>
    );
  });
}

export function weightStyle(weight: unknown): CSSProperties {
  return typeof weight === "number" && weight > 0 ? { flexGrow: weight, flexBasis: 0 } : {};
}

export function accessibilityProps(accessibility: unknown) {
  const value = accessibility as { label?: string; description?: string } | undefined;
  return {
    "aria-label": value?.label,
    "aria-description": value?.description,
  };
}

const iconMap: Record<string, LucideIcon> = {
  alert: TriangleAlert,
  bot: Bot,
  building: Building2,
  calendar: Calendar,
  check: Check,
  complete: CheckCircle2,
  danger: XCircle,
  document: FileText,
  help: HelpCircle,
  image: ImageIcon,
  info: Info,
  insight: Lightbulb,
  link: Link,
  next: ArrowRight,
  search: Search,
  sparkles: Sparkles,
  star: Star,
  trend: TrendingUp,
  user: User,
};

export function ValuzIcon({ name, size = 18 }: { name?: string; size?: number }) {
  const Icon = name ? iconMap[name.toLowerCase()] ?? Circle : Circle;
  return <Icon aria-hidden="true" size={size} strokeWidth={size >= 18 ? 1.5 : 2} />;
}

export function componentBasePath(context: ComponentContext) {
  return context.dataContext.path;
}

export function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    : [];
}

export function asString(value: unknown, fallback = "") {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

export function safeHref(value: unknown): string | undefined {
  const href = asString(value).trim();
  return /^(https?:\/\/|\/|\?)/.test(href) ? href : undefined;
}

/**
 * Resolve one safe URL for both desktop's HashRouter and WebUI's
 * BrowserRouter. Query-only links deliberately retain the current host path
 * and query (for example a finance document adds `doc=` without leaving the
 * workbench that opened it).
 */
export function linkAttributes(value: unknown): {
  href: string;
  target?: "_blank";
  rel?: string;
} | null {
  const safe = safeHref(value);
  if (!safe) return null;
  if (/^https?:\/\//.test(safe)) {
    return { href: safe, target: "_blank", rel: "noreferrer" };
  }
  if (typeof window === "undefined") return { href: safe };

  const hashRoute = window.location.hash.startsWith("#/");
  if (safe.startsWith("/")) {
    return { href: hashRoute ? `#${safe}` : safe };
  }

  const current = hashRoute
    ? window.location.hash.slice(1)
    : `${window.location.pathname}${window.location.search}`;
  const [pathname, currentQuery = ""] = current.split("?", 2);
  const params = new URLSearchParams(currentQuery);
  for (const [key, entry] of new URLSearchParams(safe.slice(1))) {
    params.set(key, entry);
  }
  const query = params.toString();
  const href = `${pathname || "/"}${query ? `?${query}` : ""}`;
  return { href: hashRoute ? `#${href}` : href };
}

export function asBoolean(value: unknown, fallback = false) {
  return typeof value === "boolean" ? value : fallback;
}

export function invokeAction(value: unknown) {
  if (typeof value === "function") value();
}
