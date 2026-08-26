/* A2UI component implementations are registry values, so this module also exports its registry list. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";
import { useId, type CSSProperties, type ReactNode } from "react";
import {
  Area,
  AreaChart as RechartsAreaChart,
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  Cell,
  ComposedChart as RechartsComposedChart,
  Funnel,
  FunnelChart as RechartsFunnelChart,
  LabelList,
  Legend,
  Line,
  LineChart as RechartsLineChart,
  Pie,
  PieChart as RechartsPieChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart as RechartsRadarChart,
  RadialBar,
  RadialBarChart as RechartsRadialChart,
  Rectangle,
  ResponsiveContainer,
  Sankey as RechartsSankey,
  Scatter,
  ScatterChart as RechartsScatterChart,
  Tooltip,
  Treemap as RechartsTreemap,
  XAxis,
  YAxis,
  ZAxis,
  type BarShapeProps,
  type SankeyData,
} from "recharts";

import {
  AreaChartApi,
  BarChartApi,
  ComboChartApi,
  DonutChartApi,
  FunnelChartApi,
  GaugeChartApi,
  HeatmapChartApi,
  HorizontalBarChartApi,
  LineChartApi,
  PieChartApi,
  RadarChartApi,
  RadialChartApi,
  SankeyChartApi,
  ScatterChartApi,
  SparklineChartApi,
  TreemapChartApi,
} from "../catalog";
import { getDistributedChartColors, resolveChartSeriesVisual } from "./chart-theme";
import { accessibilityProps, asRecords, asString, linkAttributes, weightStyle } from "./shared";

const axisStyle = { fill: "var(--va2-text-body)", fontSize: 11 };
const SINGLE_BAR_MAX_SIZE = 20;
const GROUPED_BAR_MAX_SIZE = 14;
const tooltipStyle = {
  background: "var(--va2-bg)",
  border: "1px solid var(--va2-border)",
  borderRadius: "var(--va2-radius-lg)",
  boxShadow: "var(--va2-shadow-popover)",
  color: "var(--va2-text)",
  fontSize: 12,
};

function LinkedCategoryTick({
  x,
  y,
  payload,
  data,
  categoryKey,
  linkKey,
}: {
  x?: number;
  y?: number;
  payload?: { value?: unknown; index?: number };
  data: Record<string, unknown>[];
  categoryKey: string;
  linkKey: string;
}) {
  const row =
    (typeof payload?.index === "number" ? data[payload.index] : undefined) ??
    data.find((item) => asString(item[categoryKey]) === asString(payload?.value));
  const link = linkAttributes(row?.[linkKey]);
  const text = (
    <text
      x={x}
      y={y}
      dy={4}
      fill="var(--va2-text-body)"
      fontSize={11}
      textAnchor="end"
    >
      {asString(payload?.value)}
    </text>
  );
  return link ? <a {...link}>{text}</a> : text;
}

interface ChartFrameProps {
  title?: unknown;
  description?: unknown;
  height: number;
  weight?: unknown;
  accessibility?: unknown;
  children: ReactNode;
}

function ChartFrame({
  title,
  description,
  height,
  weight,
  accessibility,
  children,
}: ChartFrameProps) {
  const titleText = asString(title);
  const descriptionText = asString(description);
  return (
    <figure
      className="va2-chart"
      style={weightStyle(weight)}
      {...accessibilityProps(accessibility)}
    >
      {titleText || descriptionText ? (
        <figcaption>
          {titleText ? <strong>{titleText}</strong> : null}
          {descriptionText ? <span>{descriptionText}</span> : null}
        </figcaption>
      ) : null}
      <div className="va2-chart__canvas" style={{ height }}>
        {children}
      </div>
    </figure>
  );
}

function SeriesLegend({
  show,
  series,
}: {
  show?: boolean;
  series?: ReadonlyArray<{ key: string; label?: unknown; url?: unknown }>;
}) {
  if (show === false) return null;
  return (
    <Legend
      iconSize={8}
      wrapperStyle={{ fontSize: 11 }}
      formatter={(value, entry) => {
        const dataKey = asString(
          (entry as { dataKey?: unknown }).dataKey,
          asString(value),
        );
        const item = series?.find(
          (candidate) =>
            candidate.key === dataKey || asString(candidate.label) === asString(value),
        );
        const link = linkAttributes(item?.url);
        return link ? <a className="va2-chart__legend-link" {...link}>{asString(value)}</a> : asString(value);
      }}
    />
  );
}

function ChartTooltip({ show }: { show?: boolean }) {
  return show === false ? null : (
    <Tooltip
      contentStyle={tooltipStyle}
      cursor={{ fill: "var(--va2-chart-cursor)", stroke: "var(--va2-chart-cursor)" }}
    />
  );
}

function asNumber(value: unknown, fallback = 0) {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function maxBarSize(
  series: ReadonlyArray<{ key: string; stack?: string }>,
  stacked = false,
) {
  const groups = new Set(
    series.map((item) => item.stack ?? (stacked ? "__default-stack" : item.key)),
  );
  return groups.size > 1 ? GROUPED_BAR_MAX_SIZE : SINGLE_BAR_MAX_SIZE;
}

type BarRadius = [number, number, number, number];
type BarSeries = { key: string; stack?: string };

function resolvedStackId(series: BarSeries, stacked = false) {
  return series.stack ?? (stacked ? "default" : undefined);
}

function stackedBarRadius(
  row: Record<string, unknown>,
  currentIndex: number,
  series: ReadonlyArray<BarSeries>,
  stacked: boolean,
  radius: number,
  layout: "horizontal" | "vertical",
): BarRadius {
  const current = series[currentIndex];
  if (!current) return [0, 0, 0, 0];
  const stackId = resolvedStackId(current, stacked);
  if (stackId === undefined) {
    return layout === "horizontal"
      ? [0, radius, radius, 0]
      : [radius, radius, 0, 0];
  }

  const value = asNumber(row[current.key]);
  const sign = Math.sign(value);
  if (sign === 0) return [0, 0, 0, 0];
  const hasOuterSegment = series.slice(currentIndex + 1).some((candidate) => (
    resolvedStackId(candidate, stacked) === stackId
    && Math.sign(asNumber(row[candidate.key])) === sign
  ));
  if (hasOuterSegment) return [0, 0, 0, 0];

  if (layout === "horizontal") {
    return sign > 0
      ? [0, radius, radius, 0]
      : [radius, 0, 0, radius];
  }
  return sign > 0
    ? [radius, radius, 0, 0]
    : [0, 0, radius, radius];
}

function stackedBarShape(
  data: ReadonlyArray<Record<string, unknown>>,
  currentIndex: number,
  series: ReadonlyArray<BarSeries>,
  stacked: boolean,
  radius: number,
  layout: "horizontal" | "vertical",
) {
  return (shapeProps: BarShapeProps) => {
    const { index, isActive: _isActive, option: _option, ...rectangleProps } = shapeProps;
    void _isActive;
    void _option;
    return (
      <Rectangle
        {...rectangleProps}
        radius={stackedBarRadius(
          data[index] ?? {},
          currentIndex,
          series,
          stacked,
          radius,
          layout,
        )}
      />
    );
  };
}

function asSankeyData(value: unknown): SankeyData {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { nodes: [], links: [] };
  }
  const candidate = value as Record<string, unknown>;
  const nodes = Array.isArray(candidate.nodes)
    ? candidate.nodes.filter((node) => Boolean(node && typeof node === "object"))
    : [];
  const links = Array.isArray(candidate.links)
    ? candidate.links.flatMap((link) => {
        if (!link || typeof link !== "object") return [];
        const record = link as Record<string, unknown>;
        const source = asNumber(record.source, -1);
        const target = asNumber(record.target, -1);
        const amount = asNumber(record.value, -1);
        return source >= 0 && target >= 0 && amount >= 0
          ? [{ source, target, value: amount }]
          : [];
      })
    : [];
  return { nodes, links };
}

export const LineChart = createComponentImplementation(LineChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", props.series?.length ?? 0);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsLineChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} series={props.series} />
          {(props.series ?? []).map((series, index) => {
            const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
            return <Line
              key={series.key}
              type={series.curve ?? "monotone"}
              dataKey={series.key}
              name={asString(series.label, series.key)}
              stroke={visual.color}
              strokeDasharray={visual.strokeDasharray}
              strokeOpacity={visual.strokeOpacity}
              strokeWidth={2}
              dot={props.showDots ? { r: 3 } : false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />;
          })}
        </RechartsLineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const AreaChart = createComponentImplementation(AreaChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const gradientId = useId().replace(/:/g, "");
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", props.series?.length ?? 0);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsAreaChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          <defs>
            {(props.series ?? []).map((series, index) => {
              const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
              return (
                <linearGradient key={series.key} id={`${gradientId}-${index}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={visual.color} stopOpacity={visual.areaGradientOpacity} />
                  <stop offset="95%" stopColor={visual.color} stopOpacity={0} />
                </linearGradient>
              );
            })}
          </defs>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} series={props.series} />
          {(props.series ?? []).map((series, index) => {
            const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
            return (
              <Area
                key={series.key}
                type={series.curve ?? "monotone"}
                dataKey={series.key}
                name={asString(series.label, series.key)}
                stackId={series.stack ?? (props.stacked ? "default" : undefined)}
                stroke={visual.color}
                strokeDasharray={visual.strokeDasharray}
                strokeOpacity={visual.strokeOpacity}
                fill={`url(#${gradientId}-${index})`}
                fillOpacity={1}
                strokeWidth={2}
                isAnimationActive={false}
              />
            );
          })}
        </RechartsAreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const BarChart = createComponentImplementation(BarChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const radius = props.barRadius ?? 4;
  const barMaxSize = maxBarSize(props.series ?? [], props.stacked);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", props.series?.length ?? 0);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsBarChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} series={props.series} />
          {(props.series ?? []).map((series, index) => {
            const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
            const stackId = resolvedStackId(series, props.stacked);
            return (
              <Bar
                key={series.key}
                dataKey={series.key}
                name={asString(series.label, series.key)}
                stackId={stackId}
                fill={visual.color}
                fillOpacity={visual.strokeOpacity}
                maxBarSize={barMaxSize}
                radius={stackId === undefined ? [radius, radius, 0, 0] : [0, 0, 0, 0]}
                shape={stackId === undefined ? undefined : stackedBarShape(
                  data,
                  index,
                  props.series ?? [],
                  Boolean(props.stacked),
                  radius,
                  "vertical",
                )}
                isAnimationActive={false}
              />
            );
          })}
        </RechartsBarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const HorizontalBarChart = createComponentImplementation(HorizontalBarChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const radius = props.barRadius ?? 4;
  const barMaxSize = maxBarSize(props.series ?? [], props.stacked);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", props.series?.length ?? 0);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsBarChart data={data} layout="vertical" margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" horizontal={false} />}
          {props.showAxes !== false && <XAxis type="number" axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis dataKey={props.categoryKey} type="category" axisLine={false} tick={props.linkKey ? <LinkedCategoryTick data={data} categoryKey={props.categoryKey} linkKey={props.linkKey} /> : axisStyle} tickLine={false} width={84} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} series={props.series} />
          {(props.series ?? []).map((series, index) => {
            const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
            const stackId = resolvedStackId(series, props.stacked);
            return (
              <Bar
                key={series.key}
                dataKey={series.key}
                name={asString(series.label, series.key)}
                stackId={stackId}
                fill={visual.color}
                fillOpacity={visual.strokeOpacity}
                maxBarSize={barMaxSize}
                radius={stackId === undefined ? [0, radius, radius, 0] : [0, 0, 0, 0]}
                shape={stackId === undefined ? undefined : stackedBarShape(
                  data,
                  index,
                  props.series ?? [],
                  Boolean(props.stacked),
                  radius,
                  "horizontal",
                )}
                isAnimationActive={false}
              />
            );
          })}
        </RechartsBarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const PieChart = createComponentImplementation(PieChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", data.length);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsPieChart>
          <Pie
            data={data}
            dataKey={props.valueKey}
            nameKey={props.nameKey}
            innerRadius={0}
            outerRadius="82%"
            paddingAngle={2}
            label={props.showLabels}
            stroke="var(--va2-bg)"
            strokeWidth={2}
            isAnimationActive={false}
          >
            {data.map((_, index) => <Cell key={index} fill={paletteColors[index]} />)}
          </Pie>
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
        </RechartsPieChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const DonutChart = createComponentImplementation(DonutChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const innerRadius = `${Math.round((props.innerRadius ?? 0.56) * 100)}%`;
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", data.length);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <div className="va2-donut">
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
          <RechartsPieChart>
            <Pie
              data={data}
              dataKey={props.valueKey}
              nameKey={props.nameKey}
              innerRadius={innerRadius}
              outerRadius="84%"
              paddingAngle={2}
              label={props.showLabels}
              stroke="var(--va2-bg)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {data.map((_, index) => <Cell key={index} fill={paletteColors[index]} />)}
            </Pie>
            <ChartTooltip show={props.showTooltip} />
            <SeriesLegend show={props.showLegend} />
          </RechartsPieChart>
        </ResponsiveContainer>
        {props.centerLabel ? <strong className="va2-donut__label">{props.centerLabel}</strong> : null}
      </div>
    </ChartFrame>
  );
});

export const ComboChart = createComponentImplementation(ComboChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const gradientId = useId().replace(/:/g, "");
  const hasRightAxis = props.rightAxis || props.series?.some((series) => series.axis === "right");
  const radius = props.barRadius ?? 4;
  const barSeries = (props.series ?? []).filter((series) => series.type === "bar");
  const barMaxSize = maxBarSize(barSeries);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", props.series?.length ?? 0);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsComposedChart data={data} margin={{ top: 8, right: hasRightAxis ? 4 : 12, left: -12, bottom: 0 }}>
          <defs>
            {(props.series ?? []).flatMap((series, index) => {
              if (series.type !== "area") return [];
              const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
              return [
                <linearGradient key={series.key} id={`${gradientId}-${index}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={visual.color} stopOpacity={visual.areaGradientOpacity} />
                  <stop offset="95%" stopColor={visual.color} stopOpacity={0} />
                </linearGradient>,
              ];
            })}
          </defs>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis yAxisId="left" axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && hasRightAxis && <YAxis yAxisId="right" orientation="right" axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} series={props.series} />
          {(props.series ?? []).map((series, index) => {
            const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
            const yAxisId = series.axis ?? "left";
            if (series.type === "bar") {
              return <Bar key={series.key} dataKey={series.key} name={asString(series.label, series.key)} fill={visual.color} fillOpacity={visual.strokeOpacity} maxBarSize={barMaxSize} radius={series.stack === undefined ? [radius, radius, 0, 0] : [0, 0, 0, 0]} shape={series.stack === undefined ? undefined : stackedBarShape(data, index, props.series ?? [], false, radius, "vertical")} stackId={series.stack} yAxisId={yAxisId} isAnimationActive={false} />;
            }
            if (series.type === "area") {
              return <Area key={series.key} type={series.curve ?? "monotone"} dataKey={series.key} name={asString(series.label, series.key)} fill={`url(#${gradientId}-${index})`} fillOpacity={1} stroke={visual.color} strokeDasharray={visual.strokeDasharray} strokeOpacity={visual.strokeOpacity} strokeWidth={2} stackId={series.stack} yAxisId={yAxisId} isAnimationActive={false} />;
            }
            return <Line key={series.key} type={series.curve ?? "monotone"} dataKey={series.key} name={asString(series.label, series.key)} dot={false} stroke={visual.color} strokeDasharray={visual.strokeDasharray} strokeOpacity={visual.strokeOpacity} strokeWidth={2} yAxisId={yAxisId} isAnimationActive={false} />;
          })}
        </RechartsComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const FunnelChart = createComponentImplementation(FunnelChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", data.length);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsFunnelChart>
          <ChartTooltip show={props.showTooltip} />
          <Funnel data={data} dataKey={props.valueKey} nameKey={props.nameKey} isAnimationActive={false}>
            {data.map((_, index) => <Cell key={index} fill={paletteColors[index]} />)}
            {props.showLabels !== false ? <LabelList dataKey={props.nameKey} fill="var(--va2-text)" position="right" /> : null}
          </Funnel>
        </RechartsFunnelChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const TreemapChart = createComponentImplementation(TreemapChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const [color] = getDistributedChartColors(props.palette ?? "ocean", 1);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsTreemap data={data} dataKey={props.valueKey} nameKey={props.nameKey} fill={color} stroke="var(--va2-bg)" isAnimationActive={false}>
          <ChartTooltip show={props.showTooltip} />
        </RechartsTreemap>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const SankeyChart = createComponentImplementation(SankeyChartApi, ({ props }) => {
  const data = asSankeyData(props.data);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", 2);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsSankey
          data={data}
          node={{ fill: paletteColors[0], stroke: "var(--va2-bg)" }}
          link={{ stroke: paletteColors[1], strokeOpacity: 0.3 }}
          nodePadding={props.nodePadding ?? 18}
          nodeWidth={props.nodeWidth ?? 12}
          margin={{ top: 8, right: 24, bottom: 8, left: 24 }}
        >
          <ChartTooltip show={props.showTooltip} />
        </RechartsSankey>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const HeatmapChart = createComponentImplementation(HeatmapChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const [color] = getDistributedChartColors(props.palette ?? "ocean", 1);
  const xValues = [...new Set(data.map((item) => asString(item[props.xKey])).filter(Boolean))];
  const yValues = [...new Set(data.map((item) => asString(item[props.yKey])).filter(Boolean))];
  const values = data.map((item) => asNumber(item[props.valueKey]));
  const min = props.min ?? Math.min(...values, 0);
  const max = Math.max(props.max ?? Math.max(...values, 1), min + 1);
  const byCoordinate = new Map(data.map((item) => [`${asString(item[props.xKey])}\u0000${asString(item[props.yKey])}`, asNumber(item[props.valueKey]) ]));
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? Math.max(180, yValues.length * 44 + 42)} weight={props.weight} accessibility={props.accessibility}>
      <div className="va2-heatmap" role="grid" style={{ "--va2-heatmap-color": color, gridTemplateColumns: `minmax(72px, auto) repeat(${xValues.length}, minmax(38px, 1fr))` } as CSSProperties}>
        <span />
        {xValues.map((x) => <strong className="va2-heatmap__axis" key={x}>{x}</strong>)}
        {yValues.flatMap((y) => [
          <strong className="va2-heatmap__axis va2-heatmap__axis--row" key={`${y}-label`}>{y}</strong>,
          ...xValues.map((x) => {
            const value = byCoordinate.get(`${x}\u0000${y}`) ?? 0;
            const intensity = Math.min(Math.max((value - min) / (max - min), 0), 1);
            return (
              <span
                aria-label={`${y}, ${x}: ${value}`}
                className="va2-heatmap__cell"
                data-level={Math.round(intensity * 4)}
                key={`${x}-${y}`}
                role="gridcell"
                style={{ "--va2-heat": intensity } as CSSProperties}
                title={`${y} · ${x}: ${value}`}
              >
                {props.showValues !== false ? value : null}
              </span>
            );
          }),
        ])}
      </div>
    </ChartFrame>
  );
});

export const GaugeChart = createComponentImplementation(GaugeChartApi, ({ props }) => {
  const min = props.min ?? 0;
  const max = Math.max(props.max ?? 100, min + 1);
  const value = Math.min(Math.max(props.value ?? min, min), max);
  const percent = ((value - min) / (max - min)) * 100;
  const [color] = getDistributedChartColors(props.palette ?? "ocean", 1);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 240} weight={props.weight} accessibility={props.accessibility}>
      <div className="va2-gauge">
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 240 }}>
          <RechartsRadialChart data={[{ name: "value", value: percent }]} innerRadius="72%" outerRadius="96%" startAngle={props.startAngle ?? 210} endAngle={props.endAngle ?? -30}>
            <RadialBar dataKey="value" fill={color} background={{ fill: "var(--va2-chart-track)" }} cornerRadius={12} isAnimationActive={false} />
          </RechartsRadialChart>
        </ResponsiveContainer>
        <div className="va2-gauge__value"><strong>{value}{props.unit ?? ""}</strong><span>{Math.round(percent)}%</span></div>
      </div>
    </ChartFrame>
  );
});

export const SparklineChart = createComponentImplementation(SparklineChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", props.series?.length ?? 0);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 96} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 360, height: props.height ?? 96 }}>
        <RechartsLineChart data={data} margin={{ top: 6, right: 3, bottom: 6, left: 3 }}>
          <ChartTooltip show={props.showTooltip} />
          {(props.series ?? []).map((series, index) => {
            const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
            return <Line key={series.key} type={series.curve ?? "monotone"} dataKey={series.key} name={asString(series.label, series.key)} stroke={visual.color} strokeDasharray={visual.strokeDasharray} strokeOpacity={visual.strokeOpacity} strokeWidth={2} dot={props.showDots ? { r: 2 } : false} isAnimationActive={false} />;
          })}
        </RechartsLineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const RadarChart = createComponentImplementation(RadarChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", props.series?.length ?? 0);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsRadarChart data={data} outerRadius="72%">
          {props.showGrid !== false && <PolarGrid stroke="var(--va2-chart-grid)" />}
          <PolarAngleAxis dataKey={props.categoryKey} tick={axisStyle} />
          <PolarRadiusAxis domain={[0, props.domainMax ?? "auto"]} tick={false} axisLine={false} />
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} series={props.series} />
          {(props.series ?? []).map((series, index) => {
            const visual = resolveChartSeriesVisual(series.role, index, paletteColors);
            return (
              <Radar
                key={series.key}
                dataKey={series.key}
                name={asString(series.label, series.key)}
                stroke={visual.color}
                strokeDasharray={visual.strokeDasharray}
                strokeOpacity={visual.strokeOpacity}
                fill={visual.color}
                fillOpacity={visual.fillOpacity}
                strokeWidth={2}
                isAnimationActive={false}
              />
            );
          })}
        </RechartsRadarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const RadialChart = createComponentImplementation(RadialChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const paletteColors = getDistributedChartColors(props.palette ?? "ocean", data.length);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsRadialChart data={data} innerRadius="28%" outerRadius="88%" startAngle={props.startAngle ?? 90} endAngle={props.endAngle ?? -270}>
          <PolarRadiusAxis domain={[0, props.max ?? 100]} tick={false} axisLine={false} />
          <RadialBar dataKey={props.valueKey} name={props.nameKey} background={{ fill: "var(--va2-chart-track)" }} cornerRadius={6} isAnimationActive={false}>
            {data.map((_, index) => <Cell key={index} fill={paletteColors[index]} />)}
          </RadialBar>
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
        </RechartsRadialChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const ScatterChart = createComponentImplementation(ScatterChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const [color] = getDistributedChartColors(props.palette ?? "ocean", 1);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsScatterChart margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" />}
          <XAxis type="number" dataKey={props.xKey} name={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />
          <YAxis type="number" dataKey={props.yKey} name={props.yKey} axisLine={false} tick={axisStyle} tickLine={false} />
          {props.sizeKey && <ZAxis type="number" dataKey={props.sizeKey} range={[48, 360]} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          <Scatter name={props.seriesName ?? props.yKey} data={data} fill={color} isAnimationActive={false} />
        </RechartsScatterChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const chartComponents = [
  LineChart,
  AreaChart,
  BarChart,
  HorizontalBarChart,
  PieChart,
  DonutChart,
  ComboChart,
  FunnelChart,
  TreemapChart,
  SankeyChart,
  HeatmapChart,
  GaugeChart,
  SparklineChart,
  RadarChart,
  RadialChart,
  ScatterChart,
];
