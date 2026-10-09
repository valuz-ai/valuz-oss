/**
 * How each dsh web-client slot maps onto Valuz's UI — the basis of running
 * standard dsh UI plugins (`dsh.client`) inside Valuz.
 *
 * Every key of the dsh SlotMap (the generated fixture
 * `dsh-slot-catalog.json`, refreshed by `scripts/dsh-upstream-sync.sh`) is
 * classified exactly once:
 *
 * - **L1** — a root-scope slot whose contributions render in a Valuz host slot
 *   (or registry: a dsh main panel becomes a route, a dsh settings section a
 *   Valuz settings section) with plain owner props.
 * - **L2** — a session-scope slot with a Valuz counterpart; its contributions
 *   need the minimal dsh session hooks (sessionId, session meta, input
 *   actions) a compatibility layer derives from the Valuz session.
 * - **L3** — dsh's own layout skeleton, or a surface only the dsh UI has. A
 *   plugin that contributes here renders in the native dsh UI that the
 *   resident manager host serves (opened from Valuz's extensions page).
 *
 * `dsh-slot-map.test.ts` fails when upstream adds, renames or removes a slot,
 * or when a mapped Valuz slot stops existing — never a silent no-op.
 */
export type DshSlotTarget =
  | {
      readonly level: "L1" | "L2";
      readonly valuz: string;
      readonly note?: string;
    }
  | { readonly level: "L3"; readonly reason: string };

/** Registry targets (not slot names) a dsh slot can map onto. */
export const DSH_REGISTRY_TARGETS = [
  "registry:route",
  "registry:settingsSection",
] as const;

const skeleton = (what: string): DshSlotTarget => ({
  level: "L3",
  reason: `dsh layout skeleton (${what}) — replacing it would replace Valuz's own UI`,
});
const nativeOnly = (what: string): DshSlotTarget => ({
  level: "L3",
  reason: `${what} — rendered by the native dsh UI of the manager host`,
});

export const DSH_SLOT_MAP: Readonly<Record<string, DshSlotTarget>> = {
  // ── frame / shell ────────────────────────────────────────────────────────
  root: skeleton("root"),
  sidebar: skeleton("sidebar frame"),
  main: {
    level: "L1",
    valuz: "registry:route",
    note: "a dsh main panel id becomes a Valuz route",
  },
  "main.conversation": skeleton("conversation panel"),
  rightbar: skeleton("right sidebar frame"),
  "rightbar.session": skeleton("session right sidebar"),
  "shell.overlay": { level: "L1", valuz: "shell.overlay" },
  "shell.leading": { level: "L1", valuz: "shell.topbar.leading" },
  "shell.bottom": {
    level: "L1",
    valuz: "shell.notice",
    note: "rendered as a top strip",
  },
  "shell.quota-notice": { level: "L1", valuz: "shell.notice" },

  // ── left sidebar ─────────────────────────────────────────────────────────
  "sidebar.toggle.badge": nativeOnly("sidebar toggle badge"),
  "sidebar.brand.mark": { level: "L1", valuz: "shell.brand.mark" },
  "sidebar.brand.name": nativeOnly("brand name text"),
  "sidebar.panellist": {
    level: "L1",
    valuz: "sidebar.nav.items",
    note: "panel entries pair with `main` panels (routes)",
  },
  "sidebar.workspaces": skeleton("workspace/session list"),
  "sidebar.settings": skeleton("settings entry"),
  "sidebar.footer.action": { level: "L1", valuz: "sidebar.footer" },
  "sidebar.workspaces.directoryFlow": nativeOnly("workspace directory picker"),
  "sidebar.session.row.leading": nativeOnly("session row leading icon"),
  "sidebar.session.row.hover": nativeOnly("session row hover actions"),
  "sidebar.workspaces.session.menu.item": {
    level: "L2",
    valuz: "sidebar.session.menu-items",
  },
  "sidebar.workspaces.session.row.action": nativeOnly(
    "inline session row action",
  ),
  "sidebar.chat.conversation": skeleton("embedded chat"),

  // ── settings ─────────────────────────────────────────────────────────────
  "settings.launcher": skeleton("settings modal launcher"),
  "settings.trigger": skeleton("settings trigger"),
  "settings.header": { level: "L1", valuz: "settings.header" },
  "settings.action": { level: "L1", valuz: "settings.header" },
  "settings.close": skeleton("settings modal close"),
  "settings.section": { level: "L1", valuz: "registry:settingsSection" },
  "settings.plugins.tab": nativeOnly("dsh plugin settings tab"),
  "settings.onboarding": nativeOnly("dsh onboarding"),
  "settings.general.item": { level: "L1", valuz: "settings.general.items" },
  "settings.models.provider-card": {
    level: "L2",
    valuz: "settings.model.provider.actions",
  },
  "settings.models.sign-in": nativeOnly("DeepSeek account sign-in"),
  "settings.models.footer": {
    level: "L2",
    valuz: "settings.section.footer",
    note: "keyed to the `model` section",
  },

  // ── plugins page (dsh's own plugin manager UI) ──────────────────────────
  "plugins.add.actions": nativeOnly("dsh Plugins page"),
  "plugins.bundle.activation": nativeOnly("dsh Plugins page"),
  "plugins.item": nativeOnly("dsh Plugins page"),
  "plugins.bundle.config": nativeOnly("dsh plugin config form"),
  "plugins.row.config": nativeOnly("dsh plugin config form"),
  "plugins.detail.actions": nativeOnly("dsh Plugins page"),
  "plugins.detail.badge": nativeOnly("dsh Plugins page"),
  "plugins.detail.section": nativeOnly("dsh Plugins page"),

  // ── conversation shell ───────────────────────────────────────────────────
  "conversation.session": skeleton("conversation session"),
  "conversation.header": skeleton("conversation header"),
  "conversation.session.header": skeleton("session header"),
  "conversation.session.header.lineage": {
    level: "L2",
    valuz: "conversation.header.leading",
  },
  "conversation.session.header.actions": {
    level: "L2",
    valuz: "conversation.title.actions",
  },
  "conversation.session.header.utilities": {
    level: "L2",
    valuz: "conversation.title.actions",
  },
  "conversation.header.leading": {
    level: "L1",
    valuz: "conversation.header.leading",
  },
  "conversation.session.header.corner": {
    level: "L2",
    valuz: "conversation.title.actions",
  },
  "conversation.view": skeleton("conversation view"),
  "conversation.composer": skeleton("composer chain"),
  "conversation.hero.workspace": {
    level: "L2",
    valuz: "conversation.empty.hero",
  },
  "conversation.hero.brand.mark": {
    level: "L1",
    valuz: "conversation.empty.brand-mark",
  },
  "conversation.hero.agentPreset": nativeOnly("dsh agent preset picker"),
  "conversation.hero.workspace.directoryFlow": nativeOnly(
    "workspace directory picker",
  ),
  "conversation.input.dock": {
    level: "L2",
    valuz: "conversation.composer.dock",
  },
  "conversation.input.overlay": {
    level: "L2",
    valuz: "conversation.composer.overlay",
  },
  "conversation.composer.dock": {
    level: "L2",
    valuz: "conversation.composer.dock",
  },
  "conversation.input.left": {
    level: "L2",
    valuz: "conversation.composer.input.left",
  },
  "conversation.input.right": {
    level: "L2",
    valuz: "conversation.composer.input.right",
  },
  "conversation.input.activity": {
    level: "L2",
    valuz: "conversation.composer.input.right",
  },
  "conversation.composer.bar": skeleton("composer bar"),
  "conversation.input.attachments": {
    level: "L2",
    valuz: "conversation.composer.attachments",
  },
  "conversation.input.plan": nativeOnly("dsh plan toggle"),
  "conversation.input.permission": nativeOnly("dsh permission picker"),
  "conversation.input.model": nativeOnly("dsh model picker"),
  "conversation.approval.detail": {
    level: "L2",
    valuz: "conversation.approval.card",
  },
  "conversation.plan-review.actions": {
    level: "L2",
    valuz: "conversation.plan.actions",
  },

  // ── transcript ───────────────────────────────────────────────────────────
  "conversation.chat.node": skeleton("transcript node renderer"),
  "conversation.message.images": nativeOnly("message image strip"),
  "conversation.chat.commandview": nativeOnly("command row"),
  "conversation.chat.turnTail": {
    level: "L2",
    valuz: "conversation.turn.tail",
  },
  "conversation.chat.assistant-actions": {
    level: "L2",
    valuz: "conversation.turn.actions",
  },
  "conversation.trajectory.images": nativeOnly("trajectory images"),
  "tool.call.toolview": {
    level: "L2",
    valuz: "conversation.tool-card.{tool}",
    note: "keyed by tool name",
  },
  "tool.call.images": nativeOnly("tool image strip"),
  "tool.view.cordis": nativeOnly("cordis tool view"),
  "deliverables.file.actions": {
    level: "L2",
    valuz: "project.generatedFiles.actions",
  },
  "deliverables.review.file.actions": nativeOnly("deliverable review"),

  // ── right sidebar ────────────────────────────────────────────────────────
  "sidebar.right.pane.tab": {
    level: "L2",
    valuz: "context-panel.tabs",
    note: "keyed by tab type",
  },
  "sidebar.right.pane.tab.title": { level: "L2", valuz: "context-panel.tabs" },
  "sidebar.right.tab.guide": nativeOnly("right tab guide"),
  "sidebar.right.tab.guide.entry": nativeOnly("right tab guide"),
  "sidebar.right.tab.menu.item": nativeOnly("right tab menu"),
  "sidebar.right.tab.document": nativeOnly("document preview"),
  "sidebar.right.tab.document.actions": nativeOnly("document preview"),
  "sidebar.right.tab.document.unpreviewable": nativeOnly("document preview"),
  "sidebar.right.tab.document.action": nativeOnly("document preview"),
  "sidebar.right.tab.document.office.pdf": nativeOnly("document preview"),
  "sidebar.right.tab.files.actions": nativeOnly("file tab actions"),
};

/** The compatibility level a dsh UI plugin gets from the slots it uses. */
export function dshPluginCompatLevel(
  slots: readonly string[],
): "L1" | "L2" | "L3" {
  let level: "L1" | "L2" | "L3" = "L1";
  for (const key of slots) {
    const target = DSH_SLOT_MAP[key];
    if (target === undefined || target.level === "L3") return "L3";
    if (target.level === "L2") level = "L2";
  }
  return level;
}
