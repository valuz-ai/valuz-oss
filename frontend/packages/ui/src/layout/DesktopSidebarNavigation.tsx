import { ArrowLeft, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import { statusDotClass } from "../components/common/status-tone";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import type { NavLinkComponent } from "./AppShell";
import type { DesktopSidebarBottomItem, DesktopSidebarNavGroup } from "./DesktopSidebar";

import { sidebarDirectItems, sidebarItemIsActive, sidebarMenuItems, sidebarRowClassName } from "./desktop-sidebar-navigation";

const railClassName = (active: boolean) => cn(
  "relative flex h-9 w-9 cursor-default items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms]",
  active ? "bg-card shadow-2 dark:bg-surface-muted dark:shadow-none" : "hover:bg-surface-soft",
);

function ItemBadge({ item }: { item: DesktopSidebarBottomItem }) {
  return item.badgeCount ? (
    <span className="ml-auto flex items-center gap-1 text-micro font-medium text-ink-meta">
      <span className={cn("h-1.5 w-1.5 rounded-full animate-pulse", statusDotClass("running"))} />
      {item.badgeCount}
    </span>
  ) : item.badgeDot ? (
    <span className="ml-auto flex items-center"><span className="h-1.5 w-1.5 rounded-full bg-error" /></span>
  ) : null;
}

interface NavigationProps {
  items: DesktopSidebarBottomItem[];
  groups: DesktopSidebarNavGroup[];
  selectedGroup: DesktopSidebarNavGroup | undefined;
  onSelectGroup: (id: string | null) => void;
  activePath: string;
  collapsed: boolean;
  backLabel: string;
  LinkComponent: NavLinkComponent;
  iconFor: (id: string) => LucideIcon;
}

export function DesktopSidebarNavigation({
  items, groups, selectedGroup, onSelectGroup, activePath, collapsed,
  backLabel, LinkComponent, iconFor,
}: NavigationProps) {
  const tooltip = (key: string, label: string, node: ReactNode) => (
    <Tooltip key={key}>
      <TooltipTrigger asChild>{node}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
  const renderItem = (item: DesktopSidebarBottomItem) => {
    const Icon = iconFor(item.icon);
    const active = sidebarItemIsActive(activePath, item);
    const node = (
      <LinkComponent to={item.href} className={collapsed ? railClassName(active) : sidebarRowClassName(active)}>
        <Icon className={collapsed ? "h-4 w-4" : "h-3.5 w-3.5 shrink-0"} strokeWidth={2} />
        {collapsed && <span className="sr-only">{item.label}</span>}
        {!collapsed && <><span>{item.label}</span><ItemBadge item={item} /></>}
        {collapsed && (item.badgeCount || item.badgeDot) ? <span className={cn(
          "absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full",
          item.badgeCount ? statusDotClass("running") : "bg-error",
        )} /> : null}
      </LinkComponent>
    );
    return collapsed ? tooltip(item.id, item.label, node) : <div key={item.id}>{node}</div>;
  };
  if (selectedGroup) {
    const back = (
      <button type="button" onClick={() => onSelectGroup(null)} aria-label={backLabel}
        className={collapsed ? railClassName(false) : sidebarRowClassName()}>
        <ArrowLeft className={collapsed ? "h-4 w-4" : "h-3.5 w-3.5 shrink-0"} strokeWidth={2} />
        {!collapsed && <span>{backLabel}</span>}
      </button>
    );
    return <>
      {collapsed ? tooltip("back", backLabel, back) : back}
      {!collapsed && <div className="px-4 py-2 text-2xs text-ink-body">{selectedGroup.label}</div>}
      {sidebarMenuItems(items, selectedGroup).map(renderItem)}
    </>;
  }
  const directItems = sidebarDirectItems(items, groups);
  const directItemActive = directItems.some((item) => sidebarItemIsActive(activePath, item));
  return <>
    {directItems.map(renderItem)}
    {collapsed && items.filter((item) =>
      !["main", "library", "settings"].includes(item.group) &&
      !groups.some((group) => group.presentation === "menu" &&
        sidebarMenuItems(items, group).some((child) => child.id === item.id)),
    ).map(renderItem)}
    {groups.filter((group) => group.presentation === "menu").map((group) => {
      const children = sidebarMenuItems(items, group);
      if (!children.length) return null;
      const Icon = iconFor(group.icon ?? "knowledge");
      const active = !directItemActive && children.some((item) => sidebarItemIsActive(activePath, item));
      const badgeCount = children.reduce((count, item) => count + (item.badgeCount ?? 0), 0);
      const badgeDot = children.some((item) => item.badgeDot);
      const node = <LinkComponent to={children[0].href} onClick={() => onSelectGroup(group.id)}
        className={collapsed ? railClassName(active) : sidebarRowClassName(active)}>
        <Icon className={collapsed ? "h-4 w-4" : "h-3.5 w-3.5 shrink-0"} strokeWidth={2} />
        {!collapsed && <span>{group.label}</span>}
        {collapsed && <span className="sr-only">{group.label}</span>}
        {!collapsed && <ItemBadge item={{ id: group.id, label: group.label, href: children[0].href,
          icon: group.icon ?? "knowledge", group: "main", badgeCount, badgeDot }} />}
        {collapsed && (badgeCount || badgeDot) ? <span className={cn(
          "h-1.5 w-1.5 rounded-full",
          "absolute right-1.5 top-1.5",
          badgeCount ? statusDotClass("running") : "bg-error",
        )} /> : null}
      </LinkComponent>;
      return collapsed ? tooltip(group.id, group.label, node) : <div key={group.id}>{node}</div>;
    })}
  </>;
}
