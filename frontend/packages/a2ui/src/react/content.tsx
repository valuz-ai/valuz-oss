/* A2UI component implementations are registry values, so this module also exports its registry list. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";
import { Check, Copy } from "lucide-react";
import MarkdownIt from "markdown-it";
import { useMemo, useState } from "react";

import {
  AvatarApi,
  CalloutApi,
  CodeBlockApi,
  EmptyStateApi,
  ImageApi,
  ImageGalleryApi,
  ListBlockApi,
  MarkdownApi,
  ProgressApi,
  SkeletonApi,
  TableApi,
  TagBlockApi,
  TextContentApi,
} from "../catalog";
import {
  ValuzIcon,
  accessibilityProps,
  asRecords,
  asString,
  weightStyle,
} from "./shared";

const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true });
const defaultLinkOpen = markdown.renderer.rules.link_open;
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index]?.attrSet("target", "_blank");
  tokens[index]?.attrSet("rel", "noopener noreferrer");
  return defaultLinkOpen
    ? defaultLinkOpen(tokens, index, options, env, self)
    : self.renderToken(tokens, index, options);
};

export const TextContent = createComponentImplementation(
  TextContentApi,
  ({ props }) => {
    const Tag: "h1" | "h2" | "h3" | "h4" | "p" =
      props.variant === "h1" ||
      props.variant === "h2" ||
      props.variant === "h3" ||
      props.variant === "h4"
        ? props.variant
        : props.variant === "display"
          ? "h1"
          : "p";
    return (
      <Tag
        className="va2-text"
        data-variant={props.variant ?? "body"}
        data-tone={props.tone ?? "neutral"}
        data-align={props.align ?? "left"}
        data-truncate={props.truncate ? "true" : "false"}
        style={weightStyle(props.weight)}
        {...accessibilityProps(props.accessibility)}
      >
        {props.text}
      </Tag>
    );
  },
);

export const Markdown = createComponentImplementation(
  MarkdownApi,
  ({ props }) => {
    const html = useMemo(
      () => markdown.render(props.content ?? ""),
      [props.content],
    );
    return (
      <div
        className="va2-markdown"
        data-compact={props.compact ? "true" : "false"}
        dangerouslySetInnerHTML={{ __html: html }}
        style={weightStyle(props.weight)}
        {...accessibilityProps(props.accessibility)}
      />
    );
  },
);

export const Image = createComponentImplementation(ImageApi, ({ props }) => (
  <figure
    className="va2-image"
    data-ratio={props.aspectRatio ?? "auto"}
    data-radius={props.radius ?? "md"}
    style={weightStyle(props.weight)}
  >
    <img
      src={props.src}
      alt={props.alt}
      data-fit={props.fit ?? "cover"}
      loading="lazy"
    />
    {props.caption && <figcaption>{props.caption}</figcaption>}
  </figure>
));

export const ImageGallery = createComponentImplementation(
  ImageGalleryApi,
  ({ props }) => {
    const images = asRecords(props.images);
    return (
      <div
        className="va2-image-gallery"
        data-ratio={props.aspectRatio ?? "square"}
        style={{
          ...weightStyle(props.weight),
          gridTemplateColumns: `repeat(${props.columns ?? 3}, minmax(0, 1fr))`,
        }}
        {...accessibilityProps(props.accessibility)}
      >
        {images.map((image, index) => (
          <figure key={`${asString(image.src)}-${index}`}>
            <img
              src={asString(image.src)}
              alt={asString(image.alt)}
              loading="lazy"
            />
            {image.caption != null && (
              <figcaption>{asString(image.caption)}</figcaption>
            )}
          </figure>
        ))}
      </div>
    );
  },
);

export const TagBlock = createComponentImplementation(
  TagBlockApi,
  ({ props }) => (
    <div
      className="va2-tags"
      data-size={props.size ?? "md"}
      style={weightStyle(props.weight)}
    >
      {(props.tags ?? []).map((tag, index) => (
        <span
          className="va2-tag"
          data-tone={tag.tone ?? "neutral"}
          key={`${tag.label}-${index}`}
        >
          {asString(tag.label)}
        </span>
      ))}
    </div>
  ),
);

export const ListBlock = createComponentImplementation(
  ListBlockApi,
  ({ props }) => {
    const items = asRecords(props.items);
    const Tag = props.ordered ? "ol" : "ul";
    return (
      <Tag
        className="va2-list"
        data-divided={props.divided ? "true" : "false"}
        data-density={props.density ?? "comfortable"}
        style={weightStyle(props.weight)}
        {...accessibilityProps(props.accessibility)}
      >
        {items.map((item, index) => (
          <li
            data-tone={asString(item.tone, "neutral")}
            key={`${asString(item.title)}-${index}`}
          >
            {item.icon != null && (
              <span className="va2-list__icon">
                <ValuzIcon name={asString(item.icon)} />
              </span>
            )}
            <span className="va2-list__body">
              <strong>{asString(item.title)}</strong>
              {item.description != null && (
                <small>{asString(item.description)}</small>
              )}
            </span>
            {item.value != null && (
              <span className="va2-list__value">{asString(item.value)}</span>
            )}
          </li>
        ))}
      </Tag>
    );
  },
);

function formatCell(value: unknown, format: string | undefined) {
  if (value == null) return "—";
  if (format === "number" && typeof value === "number")
    return new Intl.NumberFormat().format(value);
  if (format === "percent" && typeof value === "number")
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)}%`;
  if (format === "currency" && typeof value === "number")
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(value);
  if (format === "date") {
    const date = new Date(String(value));
    return Number.isNaN(date.getTime())
      ? String(value)
      : new Intl.DateTimeFormat().format(date);
  }
  return asString(value, "—");
}

export const Table = createComponentImplementation(TableApi, ({ props }) => {
  const rows = asRecords(props.rows);
  return (
    <div
      className="va2-table-wrap"
      style={weightStyle(props.weight)}
      {...accessibilityProps(props.accessibility)}
    >
      <table
        className="va2-table"
        data-striped={props.striped ? "true" : "false"}
        data-compact={props.compact ? "true" : "false"}
      >
        {props.caption && <caption>{props.caption}</caption>}
        <thead>
          <tr>
            {(props.columns ?? []).map((column) => (
              <th
                key={column.key}
                data-align={column.align ?? "left"}
                style={{ width: column.width }}
              >
                {asString(column.label)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {(props.columns ?? []).map((column) => (
                <td key={column.key} data-align={column.align ?? "left"}>
                  {formatCell(row[column.key], column.format)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
});

export const CodeBlock = createComponentImplementation(
  CodeBlockApi,
  ({ props }) => {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
      if (!navigator.clipboard) return;
      await navigator.clipboard.writeText(props.code ?? "");
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    };
    const lines = (props.code ?? "").split("\n");
    return (
      <section
        className="va2-code"
        data-wrap={props.wrap ? "true" : "false"}
        style={weightStyle(props.weight)}
      >
        <header>
          <span>{props.filename ?? props.language ?? "text"}</span>
          <button type="button" onClick={copy} aria-label="Copy code">
            {copied ? <Check size={15} /> : <Copy size={15} />}
            {copied ? "Copied" : "Copy"}
          </button>
        </header>
        <pre>
          <code>
            {props.showLineNumbers
              ? lines.map((line, index) => (
                  <span className="va2-code__line" key={index}>
                    <i>{index + 1}</i>
                    {line || " "}
                  </span>
                ))
              : props.code}
          </code>
        </pre>
      </section>
    );
  },
);

export const Callout = createComponentImplementation(
  CalloutApi,
  ({ props }) => (
    <aside
      className="va2-callout"
      data-tone={props.tone ?? "neutral"}
      style={weightStyle(props.weight)}
    >
      <span className="va2-callout__icon">
        <ValuzIcon name={props.icon ?? props.tone} />
      </span>
      <div>
        {props.title && <strong>{props.title}</strong>}
        <p>{props.content}</p>
      </div>
    </aside>
  ),
);

function initials(name: string) {
  return (
    name
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || "?"
  );
}

export const Avatar = createComponentImplementation(AvatarApi, ({ props }) => (
  <div className="va2-avatar-row" style={weightStyle(props.weight)}>
    <span
      className="va2-avatar"
      data-size={props.size ?? "md"}
      data-shape={props.shape ?? "circle"}
    >
      {props.src ? <img src={props.src} alt="" /> : initials(props.name)}
    </span>
    <span className="va2-avatar-row__text">
      <strong>{props.name}</strong>
      {props.description && <small>{props.description}</small>}
    </span>
  </div>
));

export const Progress = createComponentImplementation(
  ProgressApi,
  ({ props }) => {
    const max = Math.max(Number(props.max ?? 100), 1);
    const value = Math.min(Math.max(Number(props.value ?? 0), 0), max);
    const percent = (value / max) * 100;
    return (
      <div
        className="va2-progress"
        data-tone={props.tone ?? "neutral"}
        style={weightStyle(props.weight)}
      >
        {(props.label || props.showValue !== false) && (
          <div>
            <span>{props.label}</span>
            {props.showValue !== false && (
              <strong>{Math.round(percent)}%</strong>
            )}
          </div>
        )}
        <progress max={max} value={value}>
          {percent}%
        </progress>
      </div>
    );
  },
);

export const Skeleton = createComponentImplementation(
  SkeletonApi,
  ({ props }) => {
    const lines = props.variant === "text" ? (props.lines ?? 1) : 1;
    return (
      <div
        className="va2-skeleton-group"
        style={weightStyle(props.weight)}
        aria-hidden="true"
      >
        {Array.from({ length: lines }, (_, index) => (
          <span
            className="va2-skeleton"
            data-variant={props.variant ?? "rect"}
            key={index}
            style={{
              width: props.width,
              height: props.height,
              maxWidth: index === lines - 1 && lines > 1 ? "72%" : undefined,
            }}
          />
        ))}
      </div>
    );
  },
);

export const EmptyState = createComponentImplementation(
  EmptyStateApi,
  ({ props }) => (
    <div
      className="va2-empty"
      style={weightStyle(props.weight)}
      {...accessibilityProps(props.accessibility)}
    >
      <span>
        <ValuzIcon name={props.icon ?? "sparkles"} size={22} />
      </span>
      <strong>{props.title}</strong>
      {props.description && <p>{props.description}</p>}
      {props.actionLabel && props.action && (
        <button
          className="va2-button"
          data-variant="outline"
          data-size="sm"
          type="button"
          onClick={props.action}
        >
          {props.actionLabel}
        </button>
      )}
    </div>
  ),
);

export const contentComponents = [
  TextContent,
  Markdown,
  Image,
  ImageGallery,
  TagBlock,
  ListBlock,
  Table,
  CodeBlock,
  Callout,
  Avatar,
  Progress,
  Skeleton,
  EmptyState,
];
