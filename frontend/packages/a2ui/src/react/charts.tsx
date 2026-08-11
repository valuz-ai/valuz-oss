/* A2UI component implementations are registry values, so this module also exports its registry list. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";
import type { ReactNode } from "react";
import {
  Area,
  AreaChart as RechartsAreaChart,
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  Cell,
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
  Scatter,
  ScatterChart as RechartsScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import {
  AreaChartApi,
  BarChartApi,
  HorizontalBarChartApi,
  LineChartApi,
  PieChartApi,
  RadarChartApi,
  RadialChartApi,
  ScatterChartApi,
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
  return show === false ? null : <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--va2-bg-soft)" }} />;
}

export const LineChart = createComponentImplementation(LineChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsLineChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-border)" strokeDasharray="3 3" vertical={false} />}
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
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-border)" strokeDasharray="3 3" vertical={false} />}
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
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-border)" strokeDasharray="3 3" vertical={false} />}
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
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-border)" strokeDasharray="3 3" horizontal={false} />}
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
            innerRadius={props.donut === false ? 0 : "52%"}
            outerRadius="82%"
            paddingAngle={2}
            label={props.showLabels}
            stroke="var(--va2-bg)"
            strokeWidth={2}
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

export const RadarChart = createComponentImplementation(RadarChartApi, ({ props }) => {
  const data = asRecords(props.data);
  return (
    <ChartFrame title={props.title} description={props.description} height={props.height ?? 300} weight={props.weight} accessibility={props.accessibility}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: props.height ?? 300 }}>
        <RechartsRadarChart data={data} outerRadius="72%">
          {props.showGrid !== false && <PolarGrid stroke="var(--va2-border)" />}
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
          <RadialBar dataKey={props.valueKey} name={props.nameKey} background={{ fill: "var(--va2-bg-muted)" }} cornerRadius={6}>
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
          {props.showGrid !== false && <CartesianGrid stroke="var(--va2-border)" strokeDasharray="3 3" />}
          <XAxis type="number" dataKey={props.xKey} name={props.xKey} axisLine={false} tick={axisStyle} tickLine={false} />
          <YAxis type="number" dataKey={props.yKey} name={props.yKey} axisLine={false} tick={axisStyle} tickLine={false} />
          {props.sizeKey && <ZAxis type="number" dataKey={props.sizeKey} range={[48, 360]} />}
          <ChartTooltip show={props.showTooltip} />
          <SeriesLegend show={props.showLegend} />
          <Scatter name={props.seriesName ?? props.yKey} data={data} fill={chartColors[0]} />
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
  RadarChart,
  RadialChart,
  ScatterChart,
];
