# @valuz/plugin-sdk

The SDK for **Valuz App Plugins**: a frontend module that adds buttons,
cards, tabs, pages and settings sections to the Valuz desktop app, and uses
Valuz's own backend (projects, artifacts, knowledge, conversations, connector
tools, code automations, plugin storage) instead of shipping one.

Third-party App Plugins run on local deployments (desktop / local backend) only;
the cloud does not load them. They are installed at runtime into the data
directory, are always optional, and can only add to Valuz — never remove or
replace its own surfaces.

## Quick start

```bash
valuz-plugin create acme-dashboard --id acme.dashboard   # scaffold (also writes AGENTS.md / CLAUDE.md)
cd acme-dashboard
valuz-plugin build && valuz-plugin test && valuz-plugin validate
valuz-plugin dev            # watch + link this directory into the running Valuz
valuz-plugin pack           # dist/acme.dashboard-0.1.0.zip (+ sha256)
valuz plugin app install dist/acme.dashboard-0.1.0.zip       # or Settings → Plugins → App Plugins
```

`valuz-plugin` is this package's bin (`bin/valuz-plugin.mjs`); inside a Valuz
session it is on the agent's PATH. `valuz plugin app …` manages App Plugins through the Valuz CLI.
Agent Plugins bundle skills and MCP connectors and use `valuz plugin agent …`;
plugin origin (official or third-party) is independent of the plugin type.

## Package layout

| Path | What |
|---|---|
| `valuz-plugin.json` | Manifest — schema: [`valuz-plugin.schema.json`](valuz-plugin.schema.json) (rules a JSON Schema cannot express are listed in its `x-valuz-rules`) |
| `src/index.tsx` | `export default definePlugin({ id, apply(ctx) { … } })` |
| `frontend/` | Build output (`index.js`, `index.css`) — what Valuz loads |
| `locales/<lang>.json` | The plugin's texts (`useTranslation()`); placeholders `{{name}}` |
| `automations/` | Scripts of code automations declared in the manifest |

The shipped zip contains `valuz-plugin.json`, `frontend/`, the locales
directory, `automations/`, the icon, `README.md` and `LICENSE*` — sources,
tests, `node_modules` and dot-files are not packed.

## Writing the plugin

```tsx
import { definePlugin, pageRoute, slotComponent, useTranslation, useValuz, host, type SlotProps } from "@valuz/plugin-sdk";
import { Button } from "@valuz/plugin-sdk/ui";

function Tab({ projectId }: SlotProps<"project.detail.tabs">) {
  const valuz = useValuz();
  const { t } = useTranslation();
  // const { structuredContent } = await valuz.connectors.callTool("acme-data", "get_positions", {});
  return <Button onClick={() => host.toast({ title: t("hello") })}>{t("tab")}</Button>;
}

export default definePlugin({
  id: "acme.dashboard",
  apply(ctx) {
    ctx.registry.slot("project.detail.tabs", { id: "positions", key: "positions", label: "tab", component: slotComponent(Tab) });
    pageRoute(ctx, { id: "home", path: "/x/acme.dashboard", title: "title", icon: "dashboard" }, HomePage, { nav: true });
  },
});
```

- **Imports**: only `react`, `react-dom`, `@valuz/plugin-sdk` and `@valuz/plugin-sdk/ui` come from the host (the build rewrites them to `globalThis.__VALUZ_PLUGIN_SHARED__`); other npm packages are bundled; internal Valuz packages fail the build.
- **Ids** are local; Valuz namespaces them as `x:<plugin id>:<id>`. Pages live under `/x/<plugin id>/`; settings sections land in the Plugins group; sidebar icon ids: `assistant`, `knowledge`, `skills`, `scheduled`, `playbooks`, `activity`, `settings`, `agents`, `connectors`, `plugins`, `marketplace`, `projectTasks`, `star`, `compass`, `watchlist`, `portfolio`, `dashboard`, `globe`.
- **Public slots** (props in `src/types.ts`, `SlotProps<"…">`): `conversation.title.actions`, `conversation.header.badges`, `conversation.turn.actions`, `conversation.tool-card.{tool}` (bare tool name; `input` parsed, `outputText`, `outputJson`), `conversation.composer.plus.menu-items`, `conversation.empty.extra`, `context-panel.tabs` (keyed), `project.detail.tabs` (keyed), `project.detail.header.actions`, `resource.{type}.actions`, `shell.topbar.actions`, `task.detail.sections`.
- **React hooks**: `useTranslation()`, `useValuz()`, `usePluginConfig()`, `useHostContext()`. **Host actions**: `host.navigate / openSession / openProject / toast / confirm / openExternal / copyText / draftConversation`.

## Valuz's backend: `ctx.valuz` / `useValuz()`

Every capability must be declared in the manifest's `permissions`; a request
the manifest does not allow answers 403 (and is written to the plugin's log).

| Methods | Permission |
|---|---|
| `projects.list / get / files` | `projects:read` |
| `artifacts.list / content` | `artifacts:read` |
| `knowledge.search / get` | `knowledge:read` |
| `conversations.draft` | — (the user sends) |
| `conversations.send` / `conversations.events` | `conversations:write` / `conversations:read` |
| `connectors.list / listTools` | `connectors:read` |
| `connectors.callTool` (read-only tools) / with `{ write: true }` (the user confirms) | `connectors:call` / `connectors:write` |
| `automations.run / getRun / waitRun / latestRun` (automations the manifest declares) | `automations:run` |
| `storage.get / set / delete / list` (per user × plugin, 256 KiB per value, 10 MiB total) | `storage` |
| `notifications.post` | `notifications` |

`requires` declares what must exist (`connector:<slug>`, `edition:<id>`,
`deployment:local`, `capability:<name>`): an unmet requirement skips the plugin
instead of failing it. `config` is a JSON Schema (type object); Valuz renders a
settings form from it.

A declared `python` automation defines `run(ctx)`: `ctx["input"]` is the run
input, it returns `{"artifact": {...}}` (the run's output).

## Testing

```tsx
import { createTestHost, mockValuz } from "@valuz/plugin-sdk/testing";
const host = createTestHost({ valuz: mockValuz({ connectors: { "acme-data": { get_positions: () => ({ positions: [] }) } } }) });
await host.load(plugin);
host.capture().slots["project.detail.tabs"]; // ["x:acme.dashboard:positions"]
host.renderSlot("project.detail.tabs", { projectId: "p1", navigate: () => {} });
```

`valuz-plugin test` runs `test/**/*.test.ts(x)` with `node --test`.

## Publishing

`valuz plugin app publish dist/<zip> --scope personal|org|global [--distribution <id>]`
(signed in with `valuz auth login`, or a personal API key `--api-key vzp_…` in
CI); from a Valuz session, `extension_manager publish` (the user confirms a
card). Every submission goes through automatic checks (package, manifest,
engines, shared-module imports, secret leaks, malware heuristics); org and
global scopes are reviewed before they list.

## Build presets

`@valuz/plugin-sdk/build` exports `esbuildPreset()` and `valuzPlugin()` (Vite)
for projects that want their own bundler setup.
