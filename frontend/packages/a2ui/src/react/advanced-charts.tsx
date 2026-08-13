/* A2UI component implementations are registry values. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation as createBaseComponentImplementation } from "@a2ui/react/v0_9";
import { createContext, useContext, type CSSProperties, type ReactNode } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { BoxPlotChartApi, BulletChartApi, CalendarHeatmapChartApi, CandlestickChartApi, HistogramChartApi, NetworkGraphApi, RangeChartApi, TimeSeriesChartApi, WaterfallChartApi } from "../catalog";
import { getDistributedChartColors, resolveChartSeriesVisual, type ChartPaletteName } from "./chart-theme";
import { accessibilityProps, asRecords, asString, linkAttributes, weightStyle } from "./shared";

const axisStyle = { fill: "var(--va2-text-body)", fontSize: 10 };
const ChartPaletteContext = createContext<ChartPaletteName>("ocean");

const createPaletteAwareComponent: typeof createBaseComponentImplementation = (
  api,
  RenderComponent,
) => createBaseComponentImplementation(api, (componentProps) => (
  <ChartPaletteContext.Provider value={(componentProps.props as { palette?: ChartPaletteName }).palette ?? "ocean"}>
    <RenderComponent {...componentProps} />
  </ChartPaletteContext.Provider>
));

function number(value: unknown, fallback = 0) {
  const result = typeof value === "number" ? value : Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function scale(value: number, min: number, max: number, start: number, end: number) {
  return start + ((value - min) / Math.max(max - min, 1)) * (end - start);
}

function Frame({ title, description, height = 280, weight, accessibility, children }: { title?: unknown; description?: unknown; height?: number; weight?: unknown; accessibility?: unknown; children: ReactNode }) {
  const palette = useContext(ChartPaletteContext);
  const colors = getDistributedChartColors(palette, 3);
  const style = {
    ...weightStyle(weight),
    "--va2-pro-secondary": colors[0],
    "--va2-pro-primary": colors[1],
    "--va2-pro-tertiary": colors[2],
  } as CSSProperties;
  return <figure className="va2-chart va2-pro-chart" style={style} {...accessibilityProps(accessibility)}>{(asString(title) || asString(description)) && <figcaption>{asString(title) && <strong>{asString(title)}</strong>}{asString(description) && <span>{asString(description)}</span>}</figcaption>}<div className="va2-chart__canvas" style={{ height }}>{children}</div></figure>;
}

function EmptyPlot() { return <div className="va2-pro-chart__empty">No numerical data</div>; }

export const TimeSeriesChart = createPaletteAwareComponent(TimeSeriesChartApi, ({ props }) => {
  const raw = asRecords(props.data);
  const series = Array.isArray(props.series) ? props.series : [];
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", series.length);
  const data = props.normalize ? raw.map((row, index) => {
    const next = { ...row };
    for (const item of series) {
      const baseRow = raw.find((candidate) => Number.isFinite(Number(candidate[item.key])));
      const first = number(baseRow?.[item.key], 0);
      const current = Number(row[item.key]);
      next[item.key] = first === 0 || !Number.isFinite(current) ? undefined : (current / first) * 100;
    }
    next.__index = index;
    return next;
  }) : raw;
  return <Frame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>{data.length === 0 || series.length === 0 ? <EmptyPlot/> : <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}><LineChart data={data} margin={{ top: 10, right: 14, left: -10, bottom: 0 }}><CartesianGrid vertical={false} stroke="var(--va2-chart-grid)" strokeDasharray="3 3"/>{props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tickLine={false} tick={axisStyle}/>} {props.showAxes !== false && <YAxis domain={["auto", "auto"]} axisLine={false} tickLine={false} tick={axisStyle}/>}<Tooltip contentStyle={{ background: "var(--va2-bg)", border: "1px solid var(--va2-border)", borderRadius: "var(--va2-radius-lg)", fontSize: 11 }}/>{props.showLegend !== false && <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} formatter={(value, entry) => { const dataKey = asString((entry as { dataKey?: unknown }).dataKey, asString(value)); const item = series.find((candidate) => candidate.key === dataKey || asString(candidate.label) === asString(value)); const link = linkAttributes(item?.url); return link ? <a className="va2-chart__legend-link" {...link}>{asString(value)}</a> : asString(value); }}/>} {typeof props.referenceValue === "number" && <ReferenceLine y={props.referenceValue} stroke="var(--va2-chart-target)" strokeDasharray="2 3"/>}{series.map((item, index) => { const visual = resolveChartSeriesVisual(item.role, index, paletteColors); return <Line key={item.key} dataKey={item.key} name={asString(item.label, item.key)} type={item.curve ?? "monotone"} stroke={visual.color} strokeDasharray={visual.strokeDasharray} strokeOpacity={visual.strokeOpacity} strokeWidth={2} dot={false} activeDot={{ r: 3 }} isAnimationActive={false}/>; })}</LineChart></ResponsiveContainer>}</Frame>;
});

export const CandlestickChart = createPaletteAwareComponent(CandlestickChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const openKey = props.openKey ?? "open"; const highKey = props.highKey ?? "high"; const lowKey = props.lowKey ?? "low"; const closeKey = props.closeKey ?? "close"; const timeKey = props.timeKey ?? "time";
  const highs = data.map((item) => number(item[highKey])); const lows = data.map((item) => number(item[lowKey]));
  const min = Math.min(...lows, 0); const max = Math.max(...highs, 1); const chartBottom = props.showVolume !== false && props.volumeKey ? 192 : 224;
  const volumes = props.volumeKey ? data.map((item) => number(item[props.volumeKey!])) : []; const maxVolume = Math.max(...volumes, 1);
  return <Frame title={props.title} description={props.description} height={props.height ?? 280} weight={props.weight} accessibility={props.accessibility}>{data.length === 0 ? <EmptyPlot/> : <svg className="va2-pro-chart__svg" viewBox="0 0 720 250" role="img"><line x1="42" y1="16" x2="42" y2={chartBottom} className="va2-pro-chart__axis"/><line x1="42" y1={chartBottom} x2="708" y2={chartBottom} className="va2-pro-chart__axis"/>{data.map((item, index) => {
    const slot = 666 / data.length; const x = 42 + slot * index + slot / 2; const open = number(item[openKey]); const close = number(item[closeKey]); const high = number(item[highKey]); const low = number(item[lowKey]); const up = close >= open; const yOpen = scale(open, min, max, chartBottom - 8, 20); const yClose = scale(close, min, max, chartBottom - 8, 20); const yHigh = scale(high, min, max, chartBottom - 8, 20); const yLow = scale(low, min, max, chartBottom - 8, 20); const width = Math.max(3, Math.min(12, slot * .54));
    return <g key={index} data-trend={up ? "up" : "down"}><title>{`${asString(item[timeKey])}: O ${open} H ${high} L ${low} C ${close}`}</title><line x1={x} y1={yHigh} x2={x} y2={yLow} className="va2-candle__wick"/><rect x={x - width / 2} y={Math.min(yOpen, yClose)} width={width} height={Math.max(2, Math.abs(yClose - yOpen))} rx="1" className="va2-candle__body"/>{props.showVolume !== false && props.volumeKey && <rect x={x - width / 2} y={242 - (number(item[props.volumeKey]) / maxVolume) * 36} width={width} height={(number(item[props.volumeKey]) / maxVolume) * 36} className="va2-candle__volume"/>}{(index === 0 || index === data.length - 1 || index % Math.ceil(data.length / 5) === 0) && <text x={x} y="248" textAnchor="middle" className="va2-pro-chart__label">{asString(item[timeKey])}</text>}</g>;
  })}</svg>}</Frame>;
});

export const WaterfallChart = createPaletteAwareComponent(WaterfallChartApi, ({ props }) => {
  const data = asRecords(props.data); let running = 0;
  const points = data.map((item) => { const value = number(item[props.valueKey]); const total = Boolean(props.totalKey && item[props.totalKey]); const start = total ? 0 : running; const end = total ? value : running + value; running = end; return { item, value, start, end, total }; });
  const values = points.flatMap((point) => [point.start, point.end]); const min = Math.min(...values, 0); const max = Math.max(...values, 1);
  return <Frame title={props.title} description={props.description} height={props.height ?? 280} weight={props.weight} accessibility={props.accessibility}>{data.length === 0 ? <EmptyPlot/> : <svg className="va2-pro-chart__svg" viewBox="0 0 720 250">{points.map((point, index) => { const slot = 660 / points.length; const x = 44 + index * slot; const barWidth = Math.min(40, slot * .42); const barX = x + (slot - barWidth) / 2; const nextBarX = x + slot + (slot - barWidth) / 2; const y1 = scale(point.start, min, max, 216, 18); const y2 = scale(point.end, min, max, 216, 18); const positive = point.value >= 0; const kind = point.total ? index === 0 ? "reference" : "total" : positive ? "positive" : "negative"; return <g key={index} data-kind={kind}><rect x={barX} y={Math.min(y1, y2)} width={barWidth} height={Math.max(2, Math.abs(y2 - y1))} rx="3" className="va2-waterfall__bar"/>{index < points.length - 1 && <line x1={barX + barWidth} y1={y2} x2={nextBarX} y2={y2} className="va2-waterfall__bridge"/>}{props.showValues !== false && <text x={x + slot / 2} y={Math.min(y1, y2) - 5} textAnchor="middle" className="va2-pro-chart__value">{point.value > 0 ? "+" : ""}{point.value}</text>}<text x={x + slot / 2} y="238" textAnchor="middle" className="va2-pro-chart__label">{asString(point.item[props.nameKey])}</text></g>; })}<line x1="38" y1={scale(0, min, max, 216, 18)} x2="710" y2={scale(0, min, max, 216, 18)} className="va2-pro-chart__axis"/></svg>}</Frame>;
});

export const RangeChart = createPaletteAwareComponent(RangeChartApi, ({ props }) => {
  const data = asRecords(props.data); const values = data.flatMap((item) => [number(item[props.minKey]), number(item[props.maxKey]), props.valueKey ? number(item[props.valueKey]) : 0]); const min = Math.min(...values, 0); const max = Math.max(...values, 1);
  return <Frame title={props.title} description={props.description} height={props.height ?? 260} weight={props.weight} accessibility={props.accessibility}>{data.length === 0 ? <EmptyPlot/> : <div className="va2-range-list">{data.map((item, index) => { const low = number(item[props.minKey]); const high = number(item[props.maxKey]); const value = props.valueKey ? number(item[props.valueKey]) : undefined; const target = props.targetKey ? number(item[props.targetKey]) : undefined; return <div key={index}><span>{asString(item[props.categoryKey])}</span><div><i style={{ left: `${scale(low, min, max, 0, 100)}%`, width: `${scale(high, min, max, 0, 100) - scale(low, min, max, 0, 100)}%` }}/>{value != null && <b style={{ left: `${scale(value, min, max, 0, 100)}%` }} title={`Value ${value}`}/>} {target != null && <em style={{ left: `${scale(target, min, max, 0, 100)}%` }} title={`Target ${target}`}/>}</div><strong>{value ?? `${low}–${high}`}</strong></div>; })}</div>}</Frame>;
});

export const HistogramChart = createPaletteAwareComponent(HistogramChartApi, ({ props }) => {
  const rows = asRecords(props.data); const values = (Array.isArray(props.data) ? props.data : []).map((item) => props.valueKey && item && typeof item === "object" ? number((item as Record<string, unknown>)[props.valueKey], NaN) : number(item, NaN)).filter(Number.isFinite); const binCount = props.bins ?? 12; const min = Math.min(...values, 0); const max = Math.max(...values, 1); const step = Math.max((max - min) / binCount, .0001); const bins = Array.from({ length: binCount }, (_, index) => ({ start: min + index * step, count: 0 })); values.forEach((value) => { bins[Math.min(binCount - 1, Math.floor((value - min) / step))]!.count += 1; }); const top = Math.max(...bins.map((bin) => bin.count), 1); void rows;
  return <Frame title={props.title} description={props.description} height={props.height ?? 250} weight={props.weight} accessibility={props.accessibility}>{values.length === 0 ? <EmptyPlot/> : <div className="va2-histogram">{bins.map((bin, index) => <div key={index}><i style={{ height: `${(bin.count / top) * 100}%` }} title={`${bin.start.toFixed(1)}: ${bin.count}`}/>{(index === 0 || index === bins.length - 1 || index === Math.floor(bins.length / 2)) && <span>{bin.start.toFixed(1)}</span>}</div>)}</div>}</Frame>;
});

export const BoxPlotChart = createPaletteAwareComponent(BoxPlotChartApi, ({ props }) => {
  const data = asRecords(props.data); const values = data.flatMap((item) => [props.minKey, props.q1Key, props.medianKey, props.q3Key, props.maxKey].map((key) => number(item[key]))); const min = Math.min(...values, 0); const max = Math.max(...values, 1);
  return <Frame title={props.title} description={props.description} height={props.height ?? 250} weight={props.weight} accessibility={props.accessibility}>{data.length === 0 ? <EmptyPlot/> : <svg className="va2-pro-chart__svg" viewBox="0 0 720 250">{data.map((item, index) => { const y = 38 + index * (180 / Math.max(data.length, 1)); const xMin = scale(number(item[props.minKey]), min, max, 130, 690); const xQ1 = scale(number(item[props.q1Key]), min, max, 130, 690); const xMedian = scale(number(item[props.medianKey]), min, max, 130, 690); const xQ3 = scale(number(item[props.q3Key]), min, max, 130, 690); const xMax = scale(number(item[props.maxKey]), min, max, 130, 690); return <g key={index}><text x="118" y={y + 4} textAnchor="end" className="va2-pro-chart__label">{asString(item[props.categoryKey])}</text><line x1={xMin} y1={y} x2={xMax} y2={y} className="va2-boxplot__whisker"/><line x1={xMin} y1={y - 7} x2={xMin} y2={y + 7} className="va2-boxplot__whisker"/><line x1={xMax} y1={y - 7} x2={xMax} y2={y + 7} className="va2-boxplot__whisker"/><rect x={xQ1} y={y - 12} width={Math.max(2, xQ3 - xQ1)} height="24" rx="3" className="va2-boxplot__box"/><line x1={xMedian} y1={y - 12} x2={xMedian} y2={y + 12} className="va2-boxplot__median"/></g>; })}</svg>}</Frame>;
});

export const BulletChart = createPaletteAwareComponent(BulletChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return <Frame title={props.title} description={props.description} height={props.height ?? Math.max(150, data.length * 48 + 24)} weight={props.weight} accessibility={props.accessibility}>{data.length === 0 ? <EmptyPlot/> : <div className="va2-bullets">{data.map((item, index) => { const value = number(item[props.valueKey]); const target = number(item[props.targetKey]); const max = props.maxKey ? number(item[props.maxKey], Math.max(value, target) * 1.2) : Math.max(value, target) * 1.2; return <div key={index}><span>{asString(item[props.labelKey])}</span><div><i style={{ width: `${Math.min(100, value / max * 100)}%` }}/><b style={{ left: `${Math.min(100, target / max * 100)}%` }}/></div><strong>{value}{asString(props.unit)}</strong></div>; })}</div>}</Frame>;
});

export const CalendarHeatmapChart = createPaletteAwareComponent(CalendarHeatmapChartApi, ({ props }) => {
  const data = asRecords(props.data).slice(-(props.weeks ?? 26) * 7); const values = data.map((item) => number(item[props.valueKey])); const min = typeof props.min === "number" ? props.min : Math.min(...values, 0); const max = typeof props.max === "number" ? props.max : Math.max(...values, 1);
  return <Frame title={props.title} description={props.description} height={props.height ?? 180} weight={props.weight} accessibility={props.accessibility}>{data.length === 0 ? <EmptyPlot/> : <div className="va2-calendar-heatmap" style={{ "--va2-calendar-weeks": Math.ceil(data.length / 7) } as CSSProperties}>{data.map((item, index) => { const value = number(item[props.valueKey]); const intensity = Math.max(0, Math.min(1, (value - min) / Math.max(max - min, 1))); return <i key={index} style={{ "--va2-intensity": intensity } as CSSProperties} title={`${asString(item[props.dateKey])}: ${value}`}/>; })}</div>}</Frame>;
});

export const NetworkGraph = createPaletteAwareComponent(NetworkGraphApi, ({ props }) => {
  const input = props.data && typeof props.data === "object" && !Array.isArray(props.data) ? props.data as Record<string, unknown> : {}; const nodes = asRecords(input.nodes); const links = asRecords(input.links); const ids = new Map(nodes.map((node, index) => [asString(node.id, String(index)), index])); const position = (index: number) => ({ x: 360 + Math.cos((Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2) * 220, y: 132 + Math.sin((Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2) * 92 });
  const groupValues = [...new Set(nodes.map((node, index) => asString(node[props.groupKey ?? "group"], String(index))))];
  const groupColors = getDistributedChartColors(props.palette ?? "vivid", groupValues.length);
  const groupIndexes = new Map(groupValues.map((group, index) => [group, index]));
  return <Frame title={props.title} description={props.description} height={props.height ?? 280} weight={props.weight} accessibility={props.accessibility}>{nodes.length === 0 ? <EmptyPlot/> : <svg className="va2-pro-chart__svg va2-network" viewBox="0 0 720 265">{links.map((link, index) => { const source = ids.get(asString(link.source)) ?? number(link.source); const target = ids.get(asString(link.target)) ?? number(link.target); const a = position(source); const b = position(target); return <line key={index} x1={a.x} y1={a.y} x2={b.x} y2={b.y} style={{ strokeWidth: Math.max(1, Math.min(5, number(link.weight, 1))) }}/>; })}{nodes.map((node, index) => { const point = position(index); const radius = 8 + Math.min(15, Math.sqrt(Math.max(0, number(node[props.valueKey ?? "value"]))) * 1.2); const group = asString(node[props.groupKey ?? "group"], String(index)); const color = groupColors[groupIndexes.get(group) ?? 0]; return <g key={asString(node.id, String(index))}><circle cx={point.x} cy={point.y} r={radius} style={{ fill: color }}/>{props.showLabels !== false && <text x={point.x} y={point.y + radius + 13} textAnchor="middle">{asString(node[props.labelKey ?? "label"], asString(node.id))}</text>}</g>; })}</svg>}</Frame>;
});

export const advancedChartComponents = [TimeSeriesChart, CandlestickChart, WaterfallChart, RangeChart, HistogramChart, BoxPlotChart, BulletChart, CalendarHeatmapChart, NetworkGraph];
