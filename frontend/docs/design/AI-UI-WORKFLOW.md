# AI UI Workflow

This workflow keeps Valuz pages visually consistent when multiple people and AI agents create or upgrade frontend UI.

## Source Of Truth

- `frontend/docs/design/DESIGN.md` is the design decision record.
- `frontend/docs/design/tokens.css` is the reviewed design token source. Do not edit it during page work unless the human explicitly asks to change the source design.
- `frontend/docs/design/components.html` is the visual master for high-frequency components.
- `frontend/packages/ui/src/styles/project.css` is the runtime token implementation.
- `@valuz/ui` and `frontend/packages/ui/src/components/ui/*` are the component implementation layer.

Pages should compose existing components. Component styling belongs in `packages/ui`, not scattered through page files.

## Prompt Template

Use this template when assigning a page or component upgrade to an AI agent:

```md
Please upgrade this frontend UI according to Valuz Design Spec v2.6.

Before editing, read:
- frontend/docs/design/DESIGN.md
- frontend/docs/design/tokens.css
- frontend/docs/design/components.html
- frontend/CLAUDE.md

Constraints:
- Treat frontend/docs/design/tokens.css as read-only.
- Reuse @valuz/ui and packages/ui/src/components/ui/* before writing custom UI.
- Keep page files focused on layout, data wiring, and composition.
- Do not hardcode hex/rgb colors, arbitrary text sizes, arbitrary radii, or one-off shadows.
- Do not invent custom buttons, badges, inputs, dialogs, cards, list rows, or table styling in page code.
- If a visual pattern appears twice, extract or reuse a packages/ui component.
- Use semantic token classes only, such as text-ink-body, bg-surface, border-surface-border, bg-brand, bg-success-light, text-success-text.
- Use Button variants from the design spec: default, outline, ghost, destructive, link. Treat secondary as a migration target to outline.
- Cover default, hover, active where applicable, focus-visible, disabled, and loading states.
- All user-facing text must use i18n t() calls.

Before handoff, run:
- make design-check
- make test-all
- make typecheck
- make lint

In the final response, summarize changed files, verification results, and any remaining design exceptions.
```

## Page Upgrade Flow

1. Inventory the page.
   Identify every button, form control, dialog, card, badge/status label, list row, table, empty state, toast, popover, dropdown, and tab.

2. Map each item to a component.
   Prefer `@valuz/ui` primitives and business components. Use `components.html` to choose the closest visual master.

3. Remove page-local styling.
   Replace hand-written component styling with component props. Keep only layout classes, responsive grid/flex structure, and page-specific spacing.

4. Replace hardcoded style values.
   Remove hex/rgb colors, `text-[...]`, `rounded-[...]`, `shadow-[...]`, and ad hoc gradients unless the file is an approved exception.

5. Extract repeated patterns.
   If the same class composition appears twice, create or reuse a `packages/ui` component.

6. Check states.
   Verify default, hover, active where relevant, focus-visible, disabled, loading, empty, error, and success states.

7. Check modes and layout.
   Review light mode, dark mode, desktop, narrow widths, long translated strings, and keyboard navigation.

8. Run verification.
   Use `make design-check` plus the root quality gates: `make test-all`, `make typecheck`, and `make lint`.

## Review Checklist

- [ ] The page reads `tokens.css` as source material but does not modify it.
- [ ] Runtime token changes, if any, are made in `project.css` and remain compatible with `tokens.css`.
- [ ] Existing `@valuz/ui` components are used before custom UI.
- [ ] Page files contain layout and composition, not bespoke component styling.
- [ ] No new hardcoded hex/rgb colors were introduced.
- [ ] No new arbitrary text sizes, radii, or shadows were introduced.
- [ ] Button variants use `default`, `outline`, `ghost`, `destructive`, or `link`.
- [ ] Deprecated `secondary` usage is migrated or explicitly called out.
- [ ] Status, meta, role, and finance colors use the correct semantic token family.
- [ ] Light and dark mode both render correctly.
- [ ] Keyboard focus is visible on all interactive controls.
- [ ] Disabled and loading states are present where actions can be unavailable or async.
- [ ] Long Chinese and English strings do not overflow buttons, tabs, badges, cards, or table cells.
- [ ] User-facing text uses i18n.
- [ ] `make design-check` has been run.
- [ ] Tests, typecheck, and lint have been run.

## Exception Policy

Some surfaces may need specialized styling: syntax highlighting, charts, markdown rendering, diff views, third-party integrations, and generated artifact previews. Exceptions should be explicit and local:

- Keep the exception in the smallest possible file.
- Prefer semantic tokens before literal values.
- Add a short code comment explaining why the component system is not enough.
- Mention the exception in the handoff summary.

## Drift Control

Run these searches during cleanup to find common drift:

```bash
rg -n "#[0-9a-fA-F]{3,8}|rgb\\(|rgba\\(|text-\\[[^\\]]+\\]|rounded-\\[[^\\]]+\\]|shadow-\\[[^\\]]+\\]" frontend/packages frontend/apps
rg -n "<button|<input|<textarea|<select|role=\"button\"" frontend/packages frontend/apps -g "*.tsx"
```

Search results are triage input, not automatic failures. Fix true drift, document valid exceptions, and avoid adding new unresolved items.

## Local Pre-Commit Check

Install the local pre-commit hook once per worktree:

```bash
make install-hooks
```

After installation, every commit runs:

```bash
scripts/design-check.sh --staged
```

The hook checks that runtime tokens in `project.css` still match the reviewed variables in `tokens.css`, then scans only staged frontend additions for common style drift. This keeps new work from adding drift without requiring the historical codebase to be fully clean on day one.
