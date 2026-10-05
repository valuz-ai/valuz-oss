import { Fragment, useState, type KeyboardEvent, type ReactNode } from "react";
import type { UiElement, UiNode } from "@valuz/core";
import {
  Badge,
  Button,
  Input,
  MarkdownContent,
  NativeSelect,
  NativeSelectOption,
  Textarea,
  cn,
} from "@valuz/ui";

/**
 * Draws a UI bus element tree (hooks-and-plugin-ui.md §6.1) with Valuz design
 * system components, so plugin UI looks native. The backend has already
 * validated the tree (``modules/plugin_ui/elements.py``): unknown props are
 * gone, links and images carry safe URLs. An unknown element draws nothing.
 */

export interface UiTreeHandlers {
  /** A button press / input submit / select change. */
  onAction: (action: string, value?: unknown) => void;
  /** An action of this drawing is in flight: controls are disabled. */
  busy?: boolean;
  /** ``Default`` — what the slot shows without the plugin. */
  renderDefault?: (overrides?: Record<string, unknown>) => ReactNode;
}

const GAP = [
  "gap-0",
  "gap-1",
  "gap-2",
  "gap-3",
  "gap-4",
  "gap-5",
  "gap-6",
  "gap-7",
  "gap-8",
];
const PADDING = ["p-0", "p-1", "p-2", "p-3", "p-4", "p-5", "p-6", "p-7", "p-8"];
const ALIGN: Record<string, string> = {
  start: "items-start",
  center: "items-center",
  end: "items-end",
  stretch: "items-stretch",
};
const JUSTIFY: Record<string, string> = {
  start: "justify-start",
  center: "justify-center",
  end: "justify-end",
  between: "justify-between",
};
const TONE_TEXT: Record<string, string> = {
  default: "text-ink-body",
  muted: "text-ink-muted",
  success: "text-success-text",
  warning: "text-warning-text",
  error: "text-error-text",
  brand: "text-brand",
};
const TONE_BADGE = {
  default: "metaNeutral",
  muted: "metaOutline",
  success: "success",
  warning: "warning",
  error: "error",
  brand: "brand",
} as const;
const TEXT_SIZE: Record<string, string> = {
  xs: "text-xs",
  sm: "text-sm",
  md: "text-base",
};
const BUTTON_VARIANTS = new Set([
  "default",
  "outline",
  "ghost",
  "destructive",
  "link",
]);

const str = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;

function UiInput({
  props,
  handlers,
}: {
  props: Record<string, unknown>;
  handlers: UiTreeHandlers;
}) {
  const [value, setValue] = useState(str(props.value) ?? "");
  const action = str(props.action);
  const submit = () => {
    if (action) handlers.onAction(action, value);
  };
  const onKeyDown = (
    event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    if (props.multiline === true && !(event.metaKey || event.ctrlKey)) return;
    event.preventDefault();
    submit();
  };
  if (props.multiline === true) {
    return (
      <Textarea
        name={action}
        value={value}
        placeholder={str(props.placeholder)}
        disabled={handlers.busy}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
      />
    );
  }
  return (
    <Input
      name={action}
      value={value}
      placeholder={str(props.placeholder)}
      disabled={handlers.busy}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={onKeyDown}
    />
  );
}

function renderElement(
  node: UiElement,
  handlers: UiTreeHandlers,
  key: string,
): ReactNode {
  const props = node.props ?? {};
  const children = (node.children ?? []).map((child, index) =>
    renderNode(child, handlers, `${key}.${index}`),
  );
  switch (node.type) {
    case "Box":
      return (
        <div
          key={key}
          data-slot="plugin-ui-box"
          className={cn(
            "flex min-w-0",
            props.direction === "row" ? "flex-row" : "flex-col",
            GAP[typeof props.gap === "number" ? props.gap : 1],
            typeof props.padding === "number" && PADDING[props.padding],
            ALIGN[str(props.align) ?? ""],
            JUSTIFY[str(props.justify) ?? ""],
            props.border === true && "rounded-md border border-surface-border",
            props.wrap === true && "flex-wrap",
          )}
        >
          {children}
        </div>
      );
    case "Text":
      return (
        <span
          key={key}
          className={cn(
            TONE_TEXT[str(props.tone) ?? "default"] ?? TONE_TEXT.default,
            TEXT_SIZE[str(props.size) ?? "sm"],
            props.bold === true && "font-medium",
            props.italic === true && "italic",
            props.mono === true && "font-mono",
          )}
        >
          {str(props.text) ?? ""}
        </span>
      );
    case "Badge":
      return (
        <Badge
          key={key}
          variant={
            TONE_BADGE[
              (str(props.tone) ?? "default") as keyof typeof TONE_BADGE
            ] ?? TONE_BADGE.default
          }
        >
          {str(props.text) ?? ""}
        </Badge>
      );
    case "Button": {
      const action = str(props.action);
      const variant = str(props.variant);
      return (
        <Button
          key={key}
          size="sm"
          variant={
            variant && BUTTON_VARIANTS.has(variant)
              ? (variant as
                  "default" | "outline" | "ghost" | "destructive" | "link")
              : "outline"
          }
          disabled={props.disabled === true || handlers.busy || !action}
          onClick={() => {
            if (action) handlers.onAction(action);
          }}
        >
          {str(props.label) ?? ""}
        </Button>
      );
    }
    case "Input":
      return <UiInput key={key} props={props} handlers={handlers} />;
    case "Select": {
      const action = str(props.action);
      const options = Array.isArray(props.options)
        ? (props.options as { value: string; label: string }[])
        : [];
      return (
        <NativeSelect
          key={key}
          name={action}
          defaultValue={str(props.value) ?? ""}
          disabled={handlers.busy || !action}
          onChange={(event) => {
            if (action) handlers.onAction(action, event.target.value);
          }}
        >
          {str(props.placeholder) !== undefined &&
          str(props.value) === undefined ? (
            <NativeSelectOption value="" disabled>
              {str(props.placeholder)}
            </NativeSelectOption>
          ) : null}
          {options.map((option) => (
            <NativeSelectOption key={option.value} value={option.value}>
              {option.label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      );
    }
    case "Link": {
      const href = str(props.href);
      const text = str(props.text) ?? href ?? "";
      if (!href) return <span key={key}>{text}</span>;
      return (
        <a
          key={key}
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          className="text-brand underline-offset-4 hover:underline"
        >
          {text}
        </a>
      );
    }
    case "Code":
      return (
        <pre
          key={key}
          className="overflow-x-auto rounded-md bg-surface-soft p-2 font-mono text-xs text-ink-body"
        >
          <code>{str(props.code) ?? ""}</code>
        </pre>
      );
    case "Markdown":
      return <MarkdownContent key={key} content={str(props.text) ?? ""} />;
    case "Image":
      return (
        <img
          key={key}
          src={str(props.src)}
          alt={str(props.alt) ?? ""}
          width={typeof props.width === "number" ? props.width : undefined}
          className="max-w-full rounded-md"
        />
      );
    case "Default": {
      const overrides =
        props.overrides && typeof props.overrides === "object"
          ? (props.overrides as Record<string, unknown>)
          : undefined;
      return (
        <Fragment key={key}>
          {handlers.renderDefault?.(overrides) ?? null}
        </Fragment>
      );
    }
    default:
      return null;
  }
}

function renderNode(
  node: UiNode,
  handlers: UiTreeHandlers,
  key: string,
): ReactNode {
  if (typeof node === "string") return node;
  if (!node || typeof node !== "object") return null;
  return renderElement(node, handlers, key);
}

/** One plugin drawing. */
export function UiTree({
  tree,
  ...handlers
}: { tree: UiNode } & UiTreeHandlers) {
  return <>{renderNode(tree, handlers, "0")}</>;
}
