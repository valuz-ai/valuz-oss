import { cn } from "../lib/cn";
import type { DesktopSidebarBottomItem, DesktopSidebarNavGroup } from "./DesktopSidebar";

/** Shared by links and in-place menu actions: navigation structure must not
 * create a second visual style for the same sidebar rows. */
export function sidebarRowClassName(active = false, editing = false) {
  return cn(
    "relative mx-1 flex cursor-default items-center gap-[9px] px-[10px] py-[7px] text-sm font-normal text-ink-heading outline-none transition-[background-color,box-shadow] duration-[120ms] focus-visible:outline-none focus-visible:ring-0 focus-visible:shadow-[0_6px_16px_rgba(17,24,39,0.12)]",
    editing ? "" : "rounded-[7px]",
    editing ? "" : active
      ? "z-20 bg-card shadow-[0_6px_16px_rgba(17,24,39,0.12)] dark:bg-surface-muted dark:shadow-none"
      : "hover:bg-[rgba(0,0,0,0.03)] dark:hover:bg-surface-muted",
  );
}

export function sidebarItemIsActive(activePath: string, item: { href: string; activePaths?: string[] }) {
  const matches = (href: string) => activePath === href || activePath.startsWith(`${href}/`);
  return matches(item.href) || (item.activePaths ?? []).some(matches);
}

export function initialSidebarMenu(
  activePath: string,
  items: DesktopSidebarBottomItem[],
  groups: DesktopSidebarNavGroup[],
): string | null {
  if (sidebarDirectItems(items, groups).some((item) => sidebarItemIsActive(activePath, item))) return null;
  return groups.find((group) => group.presentation === "menu" && sidebarMenuItems(items, group).some(
    (item) => sidebarItemIsActive(activePath, item),
  ))?.id ?? null;
}

export function sidebarDirectItems(items: DesktopSidebarBottomItem[], groups: DesktopSidebarNavGroup[]) {
  const assignedIds = new Set(groups.filter((group) => group.presentation === "menu")
    .flatMap((group) => sidebarMenuItems(items, group).map((item) => item.id)));
  return items.filter((item) => item.group === "main" && !assignedIds.has(item.id));
}

export function sidebarMenuItems(items: DesktopSidebarBottomItem[], group: DesktopSidebarNavGroup) {
  if (group.itemIds) return group.itemIds.flatMap((id) => {
    const item = items.find((candidate) => candidate.id === id);
    return item ? [item] : [];
  });
  return items.filter((item) => item.group === group.id);
}
