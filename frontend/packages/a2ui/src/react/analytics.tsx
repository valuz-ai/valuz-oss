/* A2UI component implementations are registry values. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";
import { AlertCircle, CheckCircle2, Clock3, Database, ExternalLink, LoaderCircle } from "lucide-react";
import { useState, type CSSProperties } from "react";

import {
  CitationApi,
  ComparisonTableApi,
  ControlBarApi,
  DataInspectorApi,
  DataStateApi,
  DataTableApi,
  DescriptionListApi,
  DiffViewApi,
  MatrixTableApi,
  MetricApi,
  MetricGroupApi,
  ProvenanceBarApi,
  SourceListApi,
  SynchronizedChartGroupApi,
  TableChartToggleApi,
  TimelineApi,
} from "../catalog";
import { RenderChildren, accessibilityProps, asRecords, asString, invokeAction, linkAttributes, safeHref, weightStyle } from "./shared";

function formatCell(value: unknown, format?: string) {
  if (value == null || value === "") return "—";
  if (format === "number" && typeof value === "number") return new Intl.NumberFormat().format(value);
  if (format === "percent" && typeof value === "number") return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  if (format === "currency" && typeof value === "number") return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
  if (format === "date") {
    const date = new Date(String(value));
    return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat().format(date);
  }
  return asString(value, "—");
}

function MetricBody({ metric }: { metric: Record<string, unknown> }) {
  const trend = asString(metric.trend, "flat");
  const link = linkAttributes(metric.url);
  const body = <>
      <span className="va2-metric__label">{asString(metric.label)}</span>
      <strong className="va2-metric__value">{asString(metric.value)}</strong>
      {metric.delta != null ? <span className="va2-metric__delta">{asString(metric.delta)}</span> : null}
      {metric.description != null ? <small>{asString(metric.description)}</small> : null}
    </>;
  const common = {
    className: "va2-metric",
    "data-tone": asString(metric.tone, "neutral"),
    "data-trend": trend,
    "data-linked": link ? "true" : "false",
  };
  return link
    ? <a {...common} {...link}>{body}</a>
    : <div {...common}>{body}</div>;
}

export const Metric = createComponentImplementation(MetricApi, ({ props }) => (
  <div style={weightStyle(props.weight)} {...accessibilityProps(props.accessibility)}>
    <MetricBody metric={props as Record<string, unknown>} />
  </div>
));

export const MetricGroup = createComponentImplementation(MetricGroupApi, ({ props }) => (
  <section className="va2-metric-group" style={weightStyle(props.weight)} {...accessibilityProps(props.accessibility)}>
    {(props.title || props.description) && <header>{props.title && <h3>{props.title}</h3>}{props.description && <p>{props.description}</p>}</header>}
    <div style={{ gridTemplateColumns: `repeat(${props.columns ?? 4}, minmax(0, 1fr))` }}>
      {(props.metrics ?? []).map((metric, index) => <MetricBody key={`${asString(metric.label)}-${index}`} metric={metric as Record<string, unknown>} />)}
    </div>
  </section>
));

interface TableLikeProps {
  title?: unknown;
  description?: unknown;
  columns?: ReadonlyArray<Record<string, unknown>>;
  rows?: unknown;
  density?: string;
  stickyHeader?: boolean;
  maxHeight?: number;
  linkKey?: string;
  subjectKey?: string;
  highlightKey?: string;
  weight?: unknown;
  accessibility?: unknown;
}

function TableLike({ props, comparison = false }: { props: TableLikeProps; comparison?: boolean }) {
  const rows = asRecords(props.rows);
  const columns = props.columns ?? [];
  return (
    <section className="va2-data-panel" style={weightStyle(props.weight)} {...accessibilityProps(props.accessibility)}>
      {(asString(props.title) || asString(props.description)) && <header>{asString(props.title) && <h3>{asString(props.title)}</h3>}{asString(props.description) && <p>{asString(props.description)}</p>}</header>}
      <div className="va2-data-table-wrap" style={{ maxHeight: props.maxHeight }}>
        <table className="va2-data-table" data-density={props.density ?? "comfortable"} data-sticky={props.stickyHeader ? "true" : "false"}>
          <thead><tr>{columns.map((column) => <th key={asString(column.key)} data-align={asString(column.align, "left")} style={{ width: typeof column.width === "number" ? column.width : undefined }}>{asString(column.label)}</th>)}</tr></thead>
          <tbody>{rows.map((row, index) => {
            const highlighted = comparison && props.highlightKey != null && asString(row[props.subjectKey ?? ""]) === props.highlightKey;
            const rowLink = props.linkKey ? linkAttributes(row[props.linkKey]) : null;
            return <tr key={index} data-highlight={highlighted ? "true" : "false"}>{columns.map((column, columnIndex) => {
              const key = asString(column.key);
              const format = asString(column.format, "text");
              const value = row[key];
              const change = format === "change" ? (typeof value === "number" ? (value > 0 ? "up" : value < 0 ? "down" : "flat") : "flat") : undefined;
              const content = formatCell(value, format);
              return <td key={key} data-align={asString(column.align, "left")} data-trend={change}>{columnIndex === 0 && rowLink ? <a className="va2-entity-link" {...rowLink}>{content}</a> : content}</td>;
            })}</tr>;
          })}</tbody>
        </table>
      </div>
    </section>
  );
}

export const DataTable = createComponentImplementation(DataTableApi, ({ props }) => <TableLike props={props as TableLikeProps} />);
export const ComparisonTable = createComponentImplementation(ComparisonTableApi, ({ props }) => <TableLike props={props as TableLikeProps} comparison />);

export const MatrixTable = createComponentImplementation(MatrixTableApi, ({ props }) => {
  const rows = asRecords(props.rows);
  const numeric = rows.flatMap((row) => (props.columns ?? []).slice(1).map((column) => Number(row[column.key])).filter(Number.isFinite));
  const min = Number.isFinite(Number(props.min)) ? Number(props.min) : Math.min(...numeric, 0);
  const max = Number.isFinite(Number(props.max)) ? Number(props.max) : Math.max(...numeric, 1);
  return (
    <section className="va2-data-panel" style={weightStyle(props.weight)}>
      {(asString(props.title) || asString(props.description)) && <header>{asString(props.title) && <h3>{asString(props.title)}</h3>}{asString(props.description) && <p>{asString(props.description)}</p>}</header>}
      <div className="va2-data-table-wrap"><table className="va2-matrix"><thead><tr>{(props.columns ?? []).map((column) => <th key={column.key}>{asString(column.label)}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => {
          const rowLink = props.linkKey ? linkAttributes(row[props.linkKey]) : null;
          return <tr key={rowIndex}>{(props.columns ?? []).map((column, columnIndex) => {
          const value = row[column.key];
          const amount = Number(value);
          const intensity = columnIndex === 0 || !Number.isFinite(amount) ? 0 : Math.max(0, Math.min(1, (amount - min) / Math.max(max - min, 1)));
          const content = props.showValues === false && columnIndex > 0 ? null : formatCell(value, column.format);
          return <td key={column.key} data-label={columnIndex === 0 ? "true" : "false"} style={{ "--va2-intensity": intensity } as CSSProperties}>{columnIndex === 0 && rowLink ? <a className="va2-entity-link" {...rowLink}>{content}</a> : content}</td>;
        })}</tr>;
        })}</tbody>
      </table></div>
    </section>
  );
});

export const DescriptionList = createComponentImplementation(DescriptionListApi, ({ props }) => (
  <section className="va2-description-list" style={weightStyle(props.weight)}>{asString(props.title) && <h3>{asString(props.title)}</h3>}<dl style={{ gridTemplateColumns: `repeat(${props.columns ?? 2}, minmax(0, 1fr))` }}>{(props.items ?? []).map((item, index) => <div key={`${asString(item.label)}-${index}`} data-tone={item.tone ?? "neutral"}><dt>{asString(item.label)}</dt><dd>{asString(item.value)}</dd>{asString(item.description) && <small>{asString(item.description)}</small>}</div>)}</dl></section>
));

export const Timeline = createComponentImplementation(TimelineApi, ({ props }) => (
  <section className="va2-timeline" data-compact={props.compact ? "true" : "false"} style={weightStyle(props.weight)}>{asString(props.title) && <h3>{asString(props.title)}</h3>}<ol>{(props.items ?? []).map((item, index) => {
    const link = linkAttributes(item.url);
    const title = asString(item.title);
    return <li key={`${asString(item.time)}-${index}`} data-status={item.status ?? "past"}><span className="va2-timeline__marker"/><div><time>{asString(item.time)}</time><strong>{link ? <a className="va2-entity-link" {...link}>{title}</a> : title}</strong>{asString(item.description) && <p>{asString(item.description)}</p>}{asString(item.meta) && <small>{asString(item.meta)}</small>}</div></li>;
  })}</ol></section>
));

export const DiffView = createComponentImplementation(DiffViewApi, ({ props }) => (
  <section className="va2-diff" data-mode={props.mode ?? "split"} style={weightStyle(props.weight)}>{props.title && <h3>{props.title}</h3>}<div><article data-side="before"><span>{props.beforeLabel ?? "Before"}</span><p>{props.before}</p></article><article data-side="after"><span>{props.afterLabel ?? "After"}</span><p>{props.after}</p></article></div></section>
));

export const Citation = createComponentImplementation(CitationApi, ({ props }) => {
  const link = linkAttributes(props.url);
  const body = <><b>[{props.index}]</b><span><strong>{props.label}</strong>{props.excerpt && <small>{props.excerpt}</small>}</span>{safeHref(props.url) && <ExternalLink size={14}/>}</>;
  return link ? <a className="va2-citation" {...link} style={weightStyle(props.weight)}>{body}</a> : <span className="va2-citation" style={weightStyle(props.weight)}>{body}</span>;
});

export const SourceList = createComponentImplementation(SourceListApi, ({ props }) => (
  <section className="va2-sources" data-compact={props.compact ? "true" : "false"} style={weightStyle(props.weight)}>{props.title && <h3>{props.title}</h3>}<ol>{(props.sources ?? []).map((source, index) => {
    const link = linkAttributes(source.url);
    const content = <><span className="va2-sources__index">{index + 1}</span><span><strong>{asString(source.title)}</strong><small>{[source.publisher, source.type, source.date].map((value) => asString(value)).filter(Boolean).join(" · ")}</small></span>{safeHref(source.url) && <ExternalLink size={14}/>}</>;
    return <li key={`${asString(source.title)}-${index}`}>{link ? <a {...link}>{content}</a> : <div>{content}</div>}</li>;
  })}</ol></section>
));

export const ProvenanceBar = createComponentImplementation(ProvenanceBarApi, ({ props }) => (
  <footer className="va2-provenance" data-freshness={props.freshness ?? "unknown"} style={weightStyle(props.weight)}><Database size={14}/><span>{asString(props.source)}</span><i/><time>{asString(props.asOf)}</time>{asString(props.basis) && <><i/><span>{asString(props.basis)}</span></>}</footer>
));

const stateIcons = { loading: LoaderCircle, empty: Database, partial: AlertCircle, stale: Clock3, error: AlertCircle, ready: CheckCircle2 } as const;
export const DataState = createComponentImplementation(DataStateApi, ({ props }) => {
  const Icon = stateIcons[props.state];
  return <div className="va2-data-state" data-state={props.state} style={weightStyle(props.weight)}><Icon size={19}/><div><strong>{props.title}</strong>{props.description && <p>{props.description}</p>}{typeof props.progress === "number" && <progress value={props.progress} max={100}/>}</div></div>;
});

export const ControlBar = createComponentImplementation(ControlBarApi, ({ props }) => (
  <div className="va2-control-bar" data-align={props.align ?? "start"} style={weightStyle(props.weight)}>{asString(props.label) && <span>{asString(props.label)}</span>}<div>{(props.items ?? []).map((item, index) => <button key={`${item.value}-${index}`} type="button" aria-pressed={item.active === true} disabled={item.disabled === true} onClick={() => invokeAction(item.action)}>{asString(item.label)}</button>)}</div></div>
));

export const DataInspector = createComponentImplementation(DataInspectorApi, ({ props }) => (
  <details className="va2-inspector" style={weightStyle(props.weight)}><summary>{props.title ?? "Data"}</summary><pre>{JSON.stringify(props.data, null, 2)}</pre></details>
));

export const TableChartToggle = createComponentImplementation(TableChartToggleApi, ({ props, buildChild }) => {
  const [view, setView] = useState<"chart" | "table">(props.defaultView ?? "chart");
  return <section className="va2-view-toggle" style={weightStyle(props.weight)}><div role="tablist"><button type="button" role="tab" aria-selected={view === "chart"} onClick={() => setView("chart")}>{props.chartLabel ?? "Chart"}</button><button type="button" role="tab" aria-selected={view === "table"} onClick={() => setView("table")}>{props.tableLabel ?? "Table"}</button></div><div role="tabpanel">{buildChild(view === "chart" ? props.chartChild : props.tableChild)}</div></section>;
});

export const SynchronizedChartGroup = createComponentImplementation(SynchronizedChartGroupApi, ({ props, buildChild }) => (
  <section className="va2-sync-group" data-sync-key={props.syncKey} style={weightStyle(props.weight)}>{props.title && <h3>{props.title}</h3>}<div style={{ gridTemplateColumns: `repeat(${props.columns ?? 1}, minmax(0, 1fr))` }}><RenderChildren children={props.children} buildChild={buildChild}/></div></section>
));

export const analyticsComponents = [Metric, MetricGroup, DataTable, ComparisonTable, MatrixTable, DescriptionList, Timeline, DiffView, Citation, SourceList, ProvenanceBar, DataState, ControlBar, DataInspector, TableChartToggle, SynchronizedChartGroup];
