import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createContext, useContext, useState } from "react";
import type { NavLinkComponentProps } from "./AppShell";
import { DesktopSidebar, type DesktopSidebarProps } from "./DesktopSidebar";

const items: DesktopSidebarProps["bottomItems"] = [
  { id: "assistant", label: "Assistant", href: "/assistant", icon: "assistant", group: "main" },
  { id: "scheduled", label: "Automation", href: "/scheduled", activePaths: ["/playbooks"], icon: "scheduled", group: "main" },
  { id: "activity", label: "Activity", href: "/activity", icon: "activity", group: "main", badgeCount: 2 },
  { id: "projects", label: "All projects", href: "/projects", icon: "projects", group: "main" },
  { id: "agents", label: "Agents", href: "/agents", icon: "agents", group: "library" },
  { id: "plugins", label: "Plugins", href: "/plugins", icon: "plugins", group: "library" },
  { id: "settings", label: "Settings", href: "/settings", icon: "settings", group: "settings" },
];
const groups: DesktopSidebarProps["navGroups"] = [
  { id: "work", label: "Work", presentation: "menu", icon: "portfolio", itemIds: ["scheduled", "activity", "projects", "not-installed"] },
  { id: "library", label: "Resources", presentation: "menu", icon: "knowledge" },
  { id: "empty", label: "Empty", presentation: "menu" },
];
const base: Omit<DesktopSidebarProps, "activePath"> = {
  projectGroups: [{ id: "p1", label: "Existing project", href: "/projects/p1" }],
  chats: [{ id: "s1", title: "Existing conversation", href: "/conversation/s1", kind: "chat" }],
  bottomItems: items,
  navGroups: groups,
  sidebarSections: <div>Extra history section</div>,
};
const NavigationContext = createContext<(path: string) => void>(() => {});
function TestLink({ to, children, onClick, ...props }: NavLinkComponentProps) {
  const navigate = useContext(NavigationContext);
  return <a {...props} href={to} onClick={(event) => {
    event.preventDefault();
    onClick?.(event);
    navigate(to);
  }}>{children}</a>;
}
function Harness({ props, initialPath }: { props: Partial<DesktopSidebarProps>; initialPath: string }) {
  const [path, navigate] = useState(initialPath);
  return <>
    <output data-testid="current-path">{path}</output>
    <div data-testid="body">Current page stays mounted</div>
    <NavigationContext.Provider value={navigate}>
      <DesktopSidebar {...base} {...props} LinkComponent={TestLink} activePath={path} />
    </NavigationContext.Provider>
  </>;
}
function mount(props: Partial<DesktopSidebarProps> = {}, path = "/assistant") {
  const view = (next: Partial<DesktopSidebarProps>) => <Harness props={next} initialPath={path} />;
  const result = render(view(props));
  return { ...result, update: (next: Partial<DesktopSidebarProps>) => result.rerender(view(next)) };
}

describe("DesktopSidebar contextual menus", () => {
  it("replaces only the sidebar and restores its histories when returning", async () => {
    mount();
    const body = screen.getByTestId("body");
    expect(screen.queryByText("新对话")).toBeNull();
    expect(screen.queryByText("Automation")).toBeNull();
    expect(screen.queryByText("Agents")).toBeNull();
    expect(screen.queryByText("Empty")).toBeNull();
    expect(screen.getByText("Existing project")).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: /^Work/ }));
    expect(screen.getByTestId("current-path").textContent).toBe("/scheduled");
    expect(screen.getByTestId("body")).toBe(body);
    expect(screen.queryByText("Existing project")).toBeNull();
    expect(screen.queryByText("Existing conversation")).toBeNull();
    expect(screen.queryByText("Extra history section")).toBeNull();
    expect(screen.queryByRole("link", { name: "Resources" })).toBeNull();
    const links = screen.getAllByRole("link").map((link) => link.textContent);
    expect(links).toEqual(["Automation", "Activity2", "All projects", "Settings"]);
    await userEvent.click(screen.getByRole("link", { name: "Automation" }));
    expect(screen.getByTestId("current-path").textContent).toBe("/scheduled");
    await userEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(screen.getByTestId("current-path").textContent).toBe("/scheduled");
    expect(screen.getByText("Existing conversation")).toBeTruthy();
    expect(screen.getByText("Extra history section")).toBeTruthy();
    expect(screen.getByRole("link", { name: /^Work/ }).className).toContain("bg-card");
    expect(screen.queryByText("Automation")).toBeNull();
  });

  it("resolves an initial deep link and leaves a manual return at home", async () => {
    mount({}, "/playbooks/example");
    expect(screen.getByRole("link", { name: "Automation" }).className).toContain("bg-card");
    await userEvent.click(screen.getByRole("button", { name: "返回" }));
    await userEvent.click(screen.getByRole("link", { name: "Resources" }));
    expect(screen.getByRole("link", { name: "Agents" })).toBeTruthy();
    expect(screen.queryByText("Automation")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(screen.getByTestId("current-path").textContent).toBe("/agents");
    expect(screen.getByRole("link", { name: "Resources" }).className).toContain("bg-card");
  });

  it("updates submenu membership when its owning feature unloads", async () => {
    const view = mount();
    await userEvent.click(screen.getByRole("link", { name: /^Work/ }));
    view.update({ bottomItems: items.filter((item) => item.id !== "scheduled") });
    expect(screen.queryByText("Automation")).toBeNull();
    expect(screen.getByRole("link", { name: "All projects" })).toBeTruthy();
    view.update({ bottomItems: items.filter((item) => !["scheduled", "activity", "projects"].includes(item.id)) });
    expect(screen.queryByRole("button", { name: "返回" })).toBeNull();
    expect(screen.getByText("Existing project")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /^Work/ })).toBeNull();
    view.update({});
    expect(screen.getByRole("link", { name: /^Work/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "返回" })).toBeNull();
  });

  it("keeps equivalent back and active navigation in the collapsed rail", async () => {
    mount({ collapsed: true }, "/scheduled");
    const automation = screen.getByRole("link", { name: "Automation" });
    expect(automation.className).toContain("bg-card");
    const aside = automation.closest("aside")!;
    expect(within(aside).queryByRole("link", { name: /^Work/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "返回" }));
    const work = screen.getByRole("link", { name: /^Work/ });
    expect(work.className).toContain("bg-card");
    await userEvent.click(work);
    expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();
    expect(screen.getByTestId("current-path").textContent).toBe("/scheduled");
  });

  it("does not change legacy navigation without an opted-in group", () => {
    mount({ navGroups: [] });
    expect(screen.getByText("新对话")).toBeTruthy();
    expect(screen.getByText("Automation")).toBeTruthy();
    expect(screen.getByText("Agents")).toBeTruthy();
    expect(screen.getByText("Existing project")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "返回" })).toBeNull();
  });

  it("resolves a deep link when the menu plugin registers after mount", async () => {
    const view = mount({ navGroups: [] }, "/plugins/example");
    view.update({});
    expect(screen.getByRole("link", { name: "Plugins" }).className).toContain("bg-card");
    await userEvent.click(screen.getByRole("button", { name: "返回" }));
    view.update({ bottomItems: [...items] });
    expect(screen.queryByRole("button", { name: "返回" })).toBeNull();
    expect(screen.getByRole("link", { name: "Resources" })).toBeTruthy();
  });

  it("keeps a direct main entry at home when it also matches a broad menu path", () => {
    mount({ bottomItems: items.map((item) => item.id === "assistant"
      ? { ...item, activePaths: ["/scheduled/personal"] } : item) }, "/scheduled/personal");
    expect(screen.queryByRole("button", { name: "返回" })).toBeNull();
    expect(screen.getByRole("link", { name: "Assistant" }).className).toContain("bg-card");
    expect(screen.getByRole("link", { name: /^Work/ }).className).not.toContain("bg-card");
  });

  it("opens the first remaining registered item after an earlier item unloads", async () => {
    mount({ bottomItems: items.filter((item) => item.id !== "scheduled") });
    const work = screen.getByRole("link", { name: /^Work/ });
    expect(work.getAttribute("href")).toBe("/activity");
    await userEvent.click(work);
    expect(screen.getByTestId("current-path").textContent).toBe("/activity");
    expect(screen.queryByRole("link", { name: "Automation" })).toBeNull();
  });

  it("infers the ordinary conversation menu and highlights its root after Back", async () => {
    const conversations = { id: "conversations", label: "Conversations", href: "/conversation/new",
      activePaths: ["/conversation"], icon: "assistant", group: "main" };
    mount({ bottomItems: [...items, conversations], navGroups: groups.map((group) => group.id === "work"
      ? { ...group, itemIds: ["conversations", ...(group.itemIds ?? [])] } : group) }, "/conversation/ordinary");
    expect(screen.getByRole("link", { name: "Conversations" }).className).toContain("bg-card");
    await userEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(screen.getByRole("link", { name: /^Work/ }).className).toContain("bg-card");
  });

  it("corrects a delayed direct-entry destination without overriding manual menu choices", async () => {
    const view = mount({}, "/scheduled/personal");
    expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();
    const validated = items.map((item) => item.id === "assistant"
      ? { ...item, activePaths: ["/scheduled/personal"] } : item);
    view.update({ bottomItems: validated });
    expect(screen.queryByRole("button", { name: "返回" })).toBeNull();
    expect(screen.getByRole("link", { name: "Assistant" }).className).toContain("bg-card");
    expect(screen.getByRole("link", { name: /^Work/ }).className).not.toContain("bg-card");
    await userEvent.click(screen.getByRole("link", { name: /^Work/ }));
    view.update({ bottomItems: validated.map((item) => item.id === "assistant"
      ? { ...item, activePaths: ["/scheduled"] } : item) });
    expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();
  });
});
