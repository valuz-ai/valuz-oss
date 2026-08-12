/* A2UI component implementations are registry values, so this module also exports its registry list. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";
import type { CSSProperties, ReactNode } from "react";
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
  ResponsiveContainer,
  Sankey as RechartsSankey,
  Scatter,
  ScatterChart as RechartsScatterChart,
  Tooltip,
  Treemap as RechartsTreemap,
  XAxis,
  YAxis,
  ZAxis,
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
import { accessibilityProps, asRecords, asString, weightStyle } from "./shared";

const chartColors = Array.from({ length: 8 }, (_, index) => `var(--va2-chart-${index + 1})`);
const axisStyle = { fill: "var(--va2-text-body)", fontSize: 11 };
const tooltipStyle = {
  background: "var(--va2-bg)",
  border: "1px solid var(--va2-border)",
  borderRadius: "var(--va2-radius-lg)",
  boxShadow: "var(--va2-shadow-popover)",
  color: "var(--va2-text)",
  fontSize: 12,
};

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

function SeriesLegend({ show }: { show?: boolean }) {
  return show === false ? null : <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />;
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
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsLineChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          {(props.series ?? []).map((series, index) => (
            <Line
              key={series.key}
              type={series.curve ?? "monotone"}
              dataKey={series.key}
              name={asString(series.label, series.key)}
              stroke={series.color ?? chartColors[index % chartColors.length]}
              strokeWidth={2}
              dot={props.showDots ? { r: 3 } : false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          ))}
        </RechartsLineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const AreaChart = createComponentImplementation(AreaChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsAreaChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          {(props.series ?? []).map((series, index) => {
            const color = series.color ?? chartColors[index % chartColors.length];
            return (
              <Area
                key={series.key}
                type={series.curve ?? "monotone"}
                dataKey={series.key}
                name={asString(series.label, series.key)}
                stackId={series.stack ?? (props.stacked ? "default" : undefined)}
                stroke={color}
                fill={color}
                fillOpacity={0.16}
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
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsBarChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          {(props.series ?? []).map((series, index) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={asString(series.label, series.key)}
              stackId={series.stack ?? (props.stacked ? "default" : undefined)}
              fill={series.color ?? chartColors[index % chartColors.length]}
              radius={[radius, radius, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </RechartsBarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const HorizontalBarChart = createComponentImplementation(HorizontalBarChartApi, ({ props }) => {
  const data = asRecords(props.data);
  const radius = props.barRadius ?? 4;
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsBarChart data={data} layout="vertical" margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" horizontal={false} />}
          {props.showAxes !== false && <XAxis type="number" axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis dataKey={props.categoryKey} type="category" axisLine={false} tick={axisStyle} tickLine={false} width={84} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          {(props.series ?? []).map((series, index) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={asString(series.label, series.key)}
              stackId={series.stack ?? (props.stacked ? "default" : undefined)}
              fill={series.color ?? chartColors[index % chartColors.length]}
              radius={[0, radius, radius, 0]}
              isAnimationActive={false}
            />
          ))}
        </RechartsBarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const PieChart = createComponentImplementation(PieChartApi, ({ props }) => {
  const data = asRecords(props.data);
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
            {data.map((_, index) => <Cell key={index} fill={chartColors[index % chartColors.length]} />)}
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
              {data.map((_, index) => <Cell key={index} fill={chartColors[index % chartColors.length]} />)}
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
  const hasRightAxis = props.rightAxis || props.series?.some((series) => series.axis === "right");
  const radius = props.barRadius ?? 4;
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsComposedChart data={data} margin={{ top: 8, right: hasRightAxis ? 4 : 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-chart-grid)" strokeDasharray="3 3" vertical={false} />}
          {props.showAxes !== false && <XAxis dataKey={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && <YAxis yAxisId="left" axisLine={false} tick={axisStyle} tickLine={false} />}
          {props.showAxes !== false && hasRightAxis && <YAxis yAxisId="right" orientation="right" axisLine={false} tick={axisStyle} tickLine={false} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          {(props.series ?? []).map((series, index) => {
            const color = series.color ?? chartColors[index % chartColors.length];
            const yAxisId = series.axis ?? "left";
            if (series.type === "bar") {
              return <Bar key={series.key} dataKey={series.key} name={asString(series.label, series.key)} fill={color} radius={[radius, radius, 0, 0]} stackId={series.stack} yAxisId={yAxisId} isAnimationActive={false} />;
            }
            if (series.type === "area") {
              return <Area key={series.key} type={series.curve ?? "monotone"} dataKey={series.key} name={asString(series.label, series.key)} fill={color} fillOpacity={0.14} stroke={color} strokeWidth={2} stackId={series.stack} yAxisId={yAxisId} isAnimationActive={false} />;
            }
            return <Line key={series.key} type={series.curve ?? "monotone"} dataKey={series.key} name={asString(series.label, series.key)} dot={false} stroke={color} strokeWidth={2} yAxisId={yAxisId} isAnimationActive={false} />;
          })}
        </RechartsComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const FunnelChart = createComponentImplementation(FunnelChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsFunnelChart>
          <ChartTooltip show={props.showTooltip} />
          <Funnel data={data} dataKey={props.valueKey} nameKey={props.nameKey} isAnimationActive={false}>
            {data.map((_, index) => <Cell key={index} fill={chartColors[index % chartColors.length]} />)}
            {props.showLabels !== false ? <LabelList dataKey={props.nameKey} fill="var(--va2-text)" position="right" /> : null}
          </Funnel>
        </RechartsFunnelChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const TreemapChart = createComponentImplementation(TreemapChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsTreemap data={data} dataKey={props.valueKey} nameKey={props.nameKey} fill={chartColors[0]} stroke="var(--va2-bg)" isAnimationActive={false}>
          <ChartTooltip show={props.showTooltip} />
        </RechartsTreemap>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const SankeyChart = createComponentImplementation(SankeyChartApi, ({ props }) => {
  const data = asSankeyData(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsSankey
          data={data}
          node={{ fill: chartColors[0], stroke: "var(--va2-bg)" }}
          link={{ stroke: chartColors[1], strokeOpacity: 0.3 }}
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
  const xValues = [...new Set(data.map((item) => asString(item[props.xKey])).filter(Boolean))];
  const yValues = [...new Set(data.map((item) => asString(item[props.yKey])).filter(Boolean))];
  const values = data.map((item) => asNumber(item[props.valueKey]));
  const min = props.min ?? Math.min(...values, 0);
  const max = Math.max(props.max ?? Math.max(...values, 1), min + 1);
  const byCoordinate = new Map(data.map((item) => [`${asString(item[props.xKey])}\u0000${asString(item[props.yKey])}`, asNumber(item[props.valueKey]) ]));
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? Math.max(180, yValues.length * 44 + 42)} weight={props.weight} accessibility={props.accessibility}>
      <div className="va2-heatmap" role="grid" style={{ gridTemplateColumns: `minmax(72px, auto) repeat(${xValues.length}, minmax(38px, 1fr))` }}>
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
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 240} weight={props.weight} accessibility={props.accessibility}>
      <div className="va2-gauge">
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 240 }}>
          <RechartsRadialChart data={[{ name: "value", value: percent }]} innerRadius="72%" outerRadius="96%" startAngle={props.startAngle ?? 210} endAngle={props.endAngle ?? -30}>
            <RadialBar dataKey="value" fill={chartColors[0]} background={{ fill: "var(--va2-chart-track)" }} cornerRadius={12} isAnimationActive={false} />
          </RechartsRadialChart>
        </ResponsiveContainer>
        <div className="va2-gauge__value"><strong>{value}{props.unit ?? ""}</strong><span>{Math.round(percent)}%</span></div>
      </div>
    </ChartFrame>
  );
});

export const SparklineChart = createComponentImplementation(SparklineChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 96} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 360, height: props.height ?? 96 }}>
        <RechartsLineChart data={data} margin={{ top: 6, right: 3, bottom: 6, left: 3 }}>
          <ChartTooltip show={props.showTooltip} />
          {(props.series ?? []).map((series, index) => (
            <Line key={series.key} type={series.curve ?? "monotone"} dataKey={series.key} name={asString(series.label, series.key)} stroke={series.color ?? chartColors[index % chartColors.length]} strokeWidth={2} dot={props.showDots ? { r: 2 } : false} isAnimationActive={false} />
          ))}
        </RechartsLineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
});

export const RadarChart = createComponentImplementation(RadarChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsRadarChart data={data} outerRadius="72%">
          {props.showGrid !== false && <PolarGrid stroke="var(--va2-chart-grid)" />}
          <PolarAngleAxis dataKey={props.categoryKey} tick={axisStyle} />
          <PolarRadiusAxis domain={[0, props.domainMax ?? "auto"]} tick={false} axisLine={false} />
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          {(props.series ?? []).map((series, index) => {
            const color = series.color ?? chartColors[index % chartColors.length];
            return (
              <Radar
                key={series.key}
                dataKey={series.key}
                name={asString(series.label, series.key)}
                stroke={color}
                fill={color}
                fillOpacity={0.12}
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
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsRadialChart data={data} innerRadius="28%" outerRadius="88%" startAngle={props.startAngle ?? 90} endAngle={props.endAngle ?? -270}>
          <PolarRadiusAxis domain={[0, props.max ?? 100]} tick={false} axisLine={false} />
          <RadialBar dataKey={props.valueKey} name={props.nameKey} background={{ fill: "var(--va2-chart-track)" }} cornerRadius={6} isAnimationActive={false}>
            {data.map((_, index) => <Cell key={index} fill={chartColors[index % chartColors.length]} />)}
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
          <Scatter name={props.seriesName ?? props.yKey} data={data} fill={chartColors[0]} isAnimationActive={false} />
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
