import type { SlotSpec } from "./slot-catalog";

/**
 * Every slot the OSS hosts (desktop + webui, through ``@valuz/app``) render.
 * Adding a host means adding its spec here; a contribution to a name that is
 * not in this table (or declared by an overlay) renders nowhere.
 *
 * Context lists what the host passes to each contribution as props.
 */
export const HOST_SLOTS: readonly SlotSpec[] = [
  // ── Shell ────────────────────────────────────────────────────────────────
  {
    name: "shell.brand.mark",
    kind: "single",
    region: "shell",
    description: "The 20px brand mark on the top-left menu trigger.",
    context: ["appName", "logoSrc"],
  },
  {
    name: "shell.brand.menu-items",
    kind: "list",
    region: "shell",
    description: "Items in the brand menu, after Help. DropdownMenuItem only.",
    context: ["navigate", "platform"],
  },
  {
    name: "shell.topbar.leading",
    kind: "list",
    region: "shell",
    description: "Top bar, left cluster after the update button (~22px items).",
    context: ["pathname", "platform"],
  },
  {
    name: "shell.topbar.actions",
    kind: "list",
    region: "shell",
    description: "Top bar, right cluster before the panel toggles.",
    context: ["pathname", "activeProjectId", "rightPanelCollapsed"],
  },
  {
    name: "shell.notice",
    kind: "list",
    region: "shell",
    description:
      "Full-width strips at the top of the main card (quota, outage).",
    context: ["pathname", "activeProjectId"],
  },
  {
    name: "shell.overlay",
    kind: "list",
    region: "shell",
    description: "App-wide layers; contributions must be fixed or portalled.",
    context: ["pathname", "navigate"],
  },

  // ── Sidebar ──────────────────────────────────────────────────────────────
  {
    name: "sidebar.header",
    kind: "list",
    region: "sidebar",
    description: "Top of the sidebar; renders in both rail and expanded modes.",
    context: ["collapsed", "activePath"],
  },
  {
    name: "sidebar.nav.items",
    kind: "list",
    region: "sidebar",
    description:
      "Custom rows after the nav items. Plain links belong in registerNavItem.",
    context: ["collapsed", "activePath"],
  },
  {
    name: "sidebar.footer",
    kind: "list",
    region: "sidebar",
    description: "Bottom of the sidebar, after the host's own footer.",
    context: ["collapsed"],
  },
  {
    name: "sidebar.chats.actions",
    kind: "list",
    region: "sidebar",
    description: "Actions on the Chats section label.",
  },
  {
    name: "sidebar.sections",
    kind: "list",
    region: "sidebar",
    description: "Extra sections after Chats (expanded sidebar only).",
    context: ["activePath"],
  },
  {
    name: "sidebar.projects.add.menu-items",
    kind: "list",
    region: "sidebar",
    description: "Items in the add-project menu.",
  },
  {
    name: "sidebar.project.menu-items",
    kind: "list",
    region: "sidebar",
    description: "Items in a project row's … menu, before Remove.",
    context: ["projectId", "project", "navigate"],
  },
  {
    name: "sidebar.session.menu-items",
    kind: "list",
    region: "sidebar",
    description: "Items in a chat row's … menu, before Delete.",
    context: ["sessionId", "kind", "href", "isRunning"],
  },

  // ── Conversation ─────────────────────────────────────────────────────────
  {
    name: "conversation.header.leading",
    kind: "list",
    region: "conversation",
    description: "Start of the conversation header's identity cluster.",
    context: ["sessionId", "session", "project", "fromTaskId"],
  },
  {
    name: "conversation.header.badges",
    kind: "list",
    region: "conversation",
    description: "Badges after the project badge in the header.",
    context: ["sessionId", "session", "project", "agentSlug"],
  },
  {
    name: "conversation.title.actions",
    kind: "list",
    region: "conversation",
    description: "Actions at the right end of the conversation header.",
    context: ["sessionId", "session", "scrollToTop", "turns"],
  },
  {
    name: "conversation.title.menu-items",
    kind: "list",
    region: "conversation",
    description:
      "Items in the title menu, before Delete. DropdownMenuItem only.",
    context: ["sessionId", "session", "turns"],
  },
  {
    name: "conversation.empty.no-model",
    kind: "single",
    region: "conversation",
    description: "Replaces the 'no model configured' empty state.",
    context: ["navigate"],
  },
  {
    name: "conversation.empty.hero",
    kind: "single",
    region: "conversation",
    description: "Replaces the new-conversation mascot and title.",
    context: ["projectId", "agentSlug", "setDraft", "variant"],
  },
  {
    name: "conversation.empty.brand-mark",
    kind: "single",
    region: "conversation",
    description: "Replaces only the new-conversation mascot image.",
  },
  {
    name: "conversation.empty.extra",
    kind: "list",
    region: "conversation",
    description: "Below the suggestions on a new conversation.",
    context: ["projectId", "agentSlug", "setDraft"],
  },
  {
    name: "conversation.turn.leading",
    kind: "list",
    region: "conversation",
    description: "At the start of a turn, beside the message it belongs to.",
    context: ["turn", "role"],
  },
  {
    name: "conversation.turn.actions",
    kind: "list",
    region: "conversation",
    description: "The finished assistant message's action row.",
    context: ["turn", "scrollToTurn"],
  },
  {
    name: "conversation.user-message.actions",
    kind: "list",
    region: "conversation",
    description: "The user message's action row.",
    context: ["turn", "sessionId"],
  },
  {
    name: "conversation.turn.tail",
    kind: "list",
    region: "conversation",
    description: "After everything else in a turn.",
    context: ["turn", "sessionId", "isLatest", "inFlight"],
  },
  {
    name: "conversation.plan.actions",
    kind: "list",
    region: "conversation",
    description: "Footer actions on the latest plan proposal.",
    context: ["turn", "sessionId", "sessionMode"],
  },
  {
    name: "conversation.selection-actions",
    kind: "list",
    region: "conversation",
    description: "Actions on a text selection in an assistant message.",
  },
  {
    name: "conversation.approval.card",
    kind: "keyed",
    region: "conversation",
    description:
      "Takes over the approval card for one subject (key = approval subject).",
    context: ["entry", "decide"],
  },
  {
    name: "conversation.approval.actions",
    kind: "list",
    region: "conversation",
    description: "Extra buttons on the default approval card.",
    context: ["pendingId", "subject", "payload"],
  },
  {
    name: "conversation.strips",
    kind: "list",
    region: "conversation",
    description: "Strips above the composer, after background tasks.",
    context: ["sessionId", "projectId", "session", "busy", "variant"],
  },

  // ── Composer ─────────────────────────────────────────────────────────────
  {
    name: "conversation.composer.dock",
    kind: "list",
    region: "composer",
    description: "Rows docked directly above the composer.",
    context: [
      "sessionId",
      "projectId",
      "agentSlug",
      "draft",
      "setDraft",
      "busy",
    ],
  },
  {
    name: "conversation.composer.overlay",
    kind: "list",
    region: "composer",
    description: "Absolutely positioned layers anchored to the composer.",
    context: ["sessionId", "draft", "setDraft"],
  },
  {
    name: "conversation.composer.input.left",
    kind: "list",
    region: "composer",
    description: "End of the composer's left toolbar (h-7, never wraps).",
    context: ["sessionId", "projectId", "draft", "setDraft", "surface"],
  },
  {
    name: "conversation.composer.input.right",
    kind: "list",
    region: "composer",
    description: "Composer's right toolbar, before Send (h-7, never wraps).",
    context: ["sessionId", "projectId", "draft", "setDraft", "surface"],
  },
  {
    name: "conversation.composer.plus.menu-items",
    kind: "list",
    region: "composer",
    description: "Items at the end of the composer's + menu.",
    context: ["sessionId", "projectId", "setDraft", "surface"],
  },
  {
    name: "conversation.composer.attachments",
    kind: "list",
    region: "composer",
    description: "A row above the editor, after the attachment chips.",
    context: ["sessionId", "projectId", "surface"],
  },
  {
    name: "conversation.composer.bar",
    kind: "list",
    region: "composer",
    description: "End of the execution-location strip under the composer.",
    context: ["targetId", "locked", "selectedProjectId", "surface"],
  },

  // ── Tool cards & generated UI ────────────────────────────────────────────
  {
    name: "conversation.tool-card.{tool}",
    kind: "list",
    region: "tool-card",
    description:
      "Replaces the generic card for one tool (bare tool name, any runtime).",
    context: [
      "tool",
      "toolUseId",
      "status",
      "input",
      "output",
      "thinking",
      "hostRef",
    ],
  },
  {
    name: "genui.artifact-binding",
    kind: "list",
    region: "tool-card",
    description: "Binds a generated-UI receipt to its rendered artifact.",
    context: [
      "receipt",
      "toolUseId",
      "status",
      "output",
      "input",
      "thinking",
      "hostRef",
    ],
  },
  {
    name: "domain.operation-card",
    kind: "list",
    region: "domain",
    description: "Renders a domain operation proposal.",
    context: ["tool"],
  },

  // ── Context panel ────────────────────────────────────────────────────────
  {
    name: "context-panel.tabs",
    kind: "keyed",
    region: "context-panel",
    description:
      "Extra tabs in the right panel (key = tab id; label via props).",
    context: ["projectId", "sessionId", "surface"],
  },
  {
    name: "context-panel.header.actions",
    kind: "list",
    region: "context-panel",
    description: "Right end of the context panel's tab header.",
    context: ["projectId", "sessionId", "surface"],
  },
  {
    name: "context-panel.sections",
    kind: "list",
    region: "context-panel",
    description: "Sections after the generated-files section.",
    context: ["projectId", "sessionId", "surface"],
  },
  {
    name: "project.generatedFiles.actions",
    kind: "list",
    region: "context-panel",
    description: "Actions on the generated-files section header.",
    context: ["generatedFiles"],
  },

  // ── Project ──────────────────────────────────────────────────────────────
  {
    name: "project.create.extra-fields",
    kind: "list",
    region: "project",
    description: "Extra fields in the create-project dialog.",
  },
  {
    name: "project.detail.header.actions",
    kind: "list",
    region: "project",
    description: "Under the project title on the project home.",
    context: ["projectId", "project", "navigate"],
  },
  {
    name: "project.detail.tabs",
    kind: "keyed",
    region: "project",
    description: "Extra tabs in the project home's history (key = tab id).",
    context: ["projectId", "navigate"],
  },
  {
    name: "project.detail.sections",
    kind: "list",
    region: "project",
    description: "Sections at the end of the project home column.",
    context: ["projectId", "project"],
  },

  // ── Task ─────────────────────────────────────────────────────────────────
  {
    name: "task.detail.header.actions",
    kind: "list",
    region: "task",
    description: "Right end of the task title row.",
    context: ["taskId", "task", "projectId", "status"],
  },
  {
    name: "task.detail.meta",
    kind: "list",
    region: "task",
    description: "Task metadata strip; bring your own separator.",
    context: ["task"],
  },
  {
    name: "task.detail.sections",
    kind: "list",
    region: "task",
    description: "Sections after the timeline.",
    context: ["task", "isCompleted"],
  },
  {
    name: "task.detail.actions",
    kind: "list",
    region: "task",
    description: "The running task's sticky action bar, before Stop.",
    context: ["task", "busy"],
  },
  {
    name: "task.panel.tabs",
    kind: "keyed",
    region: "task",
    description: "Extra tabs in the task context panel (key = tab id).",
    context: ["projectId", "runs", "members", "taskStatus"],
  },
  {
    name: "task.panel.header.actions",
    kind: "list",
    region: "task",
    description: "Right end of the task context panel's tab header.",
    context: ["projectId", "runs", "members", "taskStatus"],
  },

  // ── Resource libraries ───────────────────────────────────────────────────
  {
    name: "resource.{type}.list.actions",
    kind: "list",
    region: "resource",
    description:
      "Library header actions (h-7 icon buttons; agent/skill/connector/kb/project).",
    context: ["navigate"],
  },
  {
    name: "resource.{type}.actions",
    kind: "list",
    region: "resource",
    description: "Actions on a resource row.",
    context: ["resourceType", "resource"],
  },
  {
    name: "resource.{type}.title.badges",
    kind: "list",
    region: "resource",
    description: "Badges beside a resource's title.",
    context: ["resourceType", "resource"],
  },
  {
    name: "resource.{type}.detail.actions",
    kind: "list",
    region: "resource",
    description: "Labelled actions on a resource detail header.",
    context: ["resourceType", "resource"],
  },
  {
    name: "resource.{type}.copy.menu-items",
    kind: "list",
    region: "resource",
    description: "Items in a resource's copy menu.",
    context: ["resourceType", "resource"],
  },
  {
    name: "resource.{type}.cloud-detail",
    kind: "list",
    region: "resource",
    description: "Detail view for a cloud-only resource.",
    context: ["resourceType", "resource"],
  },
  {
    name: "resource.agent.remote-detail.actions",
    kind: "list",
    region: "resource",
    description: "Actions on a remote agent's detail header.",
    context: ["resourceType", "resource"],
  },
  {
    name: "resource.agent.remote.sections",
    kind: "list",
    region: "resource",
    description: "Sections on a remote agent's detail page.",
  },

  // ── Settings ─────────────────────────────────────────────────────────────
  {
    name: "settings.header",
    kind: "list",
    region: "settings",
    description: "Above the active settings section.",
    context: ["tab", "setTab"],
  },
  {
    name: "settings.section.header.actions",
    kind: "keyed",
    region: "settings",
    description:
      "Actions beside a settings section's title (key = section id).",
  },
  {
    name: "settings.section.footer",
    kind: "keyed",
    region: "settings",
    description: "Below a settings section (key = section id).",
    context: ["tab"],
  },
  {
    name: "settings.general.items",
    kind: "list",
    region: "settings",
    description: "Cards in General, before Shortcuts. Bring your own heading.",
  },
  {
    name: "settings.model.channels.actions",
    kind: "list",
    region: "settings",
    description: "Beside 'Add channel' in Settings → Model.",
    context: ["openAdd"],
  },
  {
    name: "settings.model.provider.actions",
    kind: "list",
    region: "settings",
    description: "Actions on a model channel row.",
    context: ["provider", "isSystem", "isConfigured"],
  },

  // ── Notifications ────────────────────────────────────────────────────────
  {
    name: "notification.card.{kind}",
    kind: "list",
    region: "notification",
    description: "Replaces the inbox card for one notification kind.",
    context: ["entry", "onNavigateAway"],
  },

  // ── Onboarding ───────────────────────────────────────────────────────────
  {
    name: "onboarding.header.actions",
    kind: "list",
    region: "onboarding",
    description:
      "Onboarding header, before Skip (must opt out of window drag).",
    context: ["step", "stepIndex"],
  },
  {
    name: "onboarding.footer.actions",
    kind: "keyed",
    region: "onboarding",
    description: "A step's footer, before Skip (key = step id).",
    context: ["step"],
  },
];
