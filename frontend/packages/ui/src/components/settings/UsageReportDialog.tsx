import { useCallback, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "../ui/chart";
import { useI18n } from "../../hooks/use-i18n";

const MODEL_COLORS = [
  "hsl(35, 90%, 55%)",
  "hsl(200, 80%, 55%)",
  "hsl(150, 70%, 45%)",
  "hsl(280, 70%, 60%)",
  "hsl(10, 80%, 55%)",
  "hsl(60, 70%, 50%)",
];

const TOKEN_COLORS = {
  cache_read_tokens: "hsl(200, 60%, 70%)",
  input_tokens: "hsl(220, 70%, 55%)",
  output_tokens: "hsl(220, 80%, 40%)",
};

export interface DailyModelUsage {
  date: string;
  request_count: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
}

export interface ModelUsageSummary {
  model: string;
  total_requests: number;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_write_tokens: number;
  daily: DailyModelUsage[];
}

export interface UsageData {
  year: number;
  month: number;
  total_tokens: number;
  total_requests: number;
  models: string[];
  overview: Array<Record<string, unknown>>;
  by_model: ModelUsageSummary[];
}

export interface UsageReportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fetchUsage: (year: number, month: number) => Promise<UsageData>;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function fillDailyData(
  daily: Array<Record<string, unknown>>,
  year: number,
  month: number,
  keys: string[],
): Array<Record<string, unknown>> {
  const daysInMonth = new Date(year, month, 0).getDate();
  const lookup = new Map<string, Record<string, unknown>>();
  for (const row of daily) {
    lookup.set(row.date as string, row);
  }
  const filled: Array<Record<string, unknown>> = [];
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const existing = lookup.get(dateStr);
    if (existing) {
      filled.push(existing);
    } else {
      const empty: Record<string, unknown> = { date: dateStr };
      for (const k of keys) empty[k] = 0;
      filled.push(empty);
    }
  }
  return filled;
}

function OverviewChart({
  data,
  models,
  year,
  month,
  t,
}: {
  data: UsageData;
  models: string[];
  year: number;
  month: number;
  t: (
    key: string,
    fallback?: string | Record<string, string | number>,
  ) => string;
}) {
  const config: ChartConfig = {};
  models.forEach((model, idx) => {
    config[model] = {
      label: model,
      color: MODEL_COLORS[idx % MODEL_COLORS.length],
    };
  });
  const chartData = fillDailyData(data.overview, year, month, models);

  return (
    <div>
      <div className="mb-1 text-sm text-muted-foreground">
        {t("ui.usageReport.totalTokens")}{" "}
        <span className="text-base font-semibold text-foreground">
          {formatNumber(data.total_tokens)}
        </span>
      </div>
      <ChartContainer config={config} className="h-[140px] w-full">
        <BarChart data={chartData}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: string) => {
              const day = parseInt(v.split("-")[2], 10);
              return day === 1 || day % 5 === 0 ? `${month}-${day}` : "";
            }}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            tickFormatter={formatTokens}
            width={50}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                labelFormatter={(_, payload) => {
                  if (!payload?.length) return "";
                  return (payload[0]?.payload?.date as string) ?? "";
                }}
              />
            }
          />
          {models.map((model, idx) => (
            <Bar
              key={model}
              dataKey={model}
              stackId="overview"
              fill={MODEL_COLORS[idx % MODEL_COLORS.length]}
              radius={idx === models.length - 1 ? [2, 2, 0, 0] : [0, 0, 0, 0]}
            />
          ))}
        </BarChart>
      </ChartContainer>
    </div>
  );
}

function ModelSection({
  model,
  colorIdx,
  t,
}: {
  model: ModelUsageSummary;
  colorIdx: number;
  t: (
    key: string,
    fallback?: string | Record<string, string | number>,
  ) => string;
}) {
  const requestConfig: ChartConfig = {
    request_count: {
      label: t("ui.usageReport.apiRequestCount"),
      color: MODEL_COLORS[colorIdx % MODEL_COLORS.length],
    },
  };

  const tokenConfig: ChartConfig = {
    cache_read_tokens: {
      label: t("ui.usageReport.cacheRead"),
      color: TOKEN_COLORS.cache_read_tokens,
    },
    input_tokens: {
      label: t("ui.usageReport.inputUncached"),
      color: TOKEN_COLORS.input_tokens,
    },
    output_tokens: {
      label: t("ui.usageReport.output"),
      color: TOKEN_COLORS.output_tokens,
    },
  };

  return (
    <div className="space-y-3 rounded-lg border border-border/50 bg-card/30 p-4">
      <h4 className="text-sm font-semibold text-foreground">{model.model}</h4>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="mb-1 text-xs text-muted-foreground">
            {t("ui.usageReport.apiRequestCount")}{" "}
            <span className="font-semibold text-foreground">
              {formatNumber(model.total_requests)}
            </span>
          </div>
          <ChartContainer config={requestConfig} className="h-[120px] w-full">
            <BarChart data={model.daily}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: string) =>
                  `${parseInt(v.split("-")[1], 10)}-${parseInt(v.split("-")[2], 10)}`
                }
              />
              <YAxis tickLine={false} axisLine={false} width={30} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <Bar
                dataKey="request_count"
                fill={MODEL_COLORS[colorIdx % MODEL_COLORS.length]}
                radius={[2, 2, 0, 0]}
              />
            </BarChart>
          </ChartContainer>
        </div>
        <div>
          <div className="mb-1 text-xs text-muted-foreground">
            Tokens{" "}
            <span className="font-semibold text-foreground">
              {formatNumber(model.total_tokens)}
            </span>
          </div>
          <ChartContainer config={tokenConfig} className="h-[120px] w-full">
            <BarChart data={model.daily}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: string) =>
                  `${parseInt(v.split("-")[1], 10)}-${parseInt(v.split("-")[2], 10)}`
                }
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tickFormatter={formatTokens}
                width={50}
              />
              <ChartTooltip content={<ChartTooltipContent />} />
              <Bar
                dataKey="cache_read_tokens"
                stackId="tokens"
                fill={TOKEN_COLORS.cache_read_tokens}
              />
              <Bar
                dataKey="input_tokens"
                stackId="tokens"
                fill={TOKEN_COLORS.input_tokens}
              />
              <Bar
                dataKey="output_tokens"
                stackId="tokens"
                fill={TOKEN_COLORS.output_tokens}
                radius={[2, 2, 0, 0]}
              />
            </BarChart>
          </ChartContainer>
        </div>
      </div>
    </div>
  );
}

export function UsageReportDialog({
  open,
  onOpenChange,
  fetchUsage,
}: UsageReportDialogProps) {
  const { t } = useI18n();
  const now = new Date();
  const [selectedMonth, setSelectedMonth] = useState(
    `${now.getFullYear()}-${now.getMonth() + 1}`,
  );
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const monthOptions = useMemo(() => {
    const opts: Array<{ label: string; value: string }> = [];
    for (let i = 0; i < 12; i++) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const label = t("ui.usageReport.monthLabel", {
        year: String(d.getFullYear()),
        month: String(d.getMonth() + 1),
      });
      opts.push({
        label,
        value: `${d.getFullYear()}-${d.getMonth() + 1}`,
      });
    }
    return opts;
  }, [t]);

  const [year, month] = useMemo(() => {
    const parts = selectedMonth.split("-");
    return [parseInt(parts[0], 10), parseInt(parts[1], 10)];
  }, [selectedMonth]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchUsage(year, month);
      setData(result);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("ui.usageReport.loadFailed"),
      );
    } finally {
      setLoading(false);
    }
  }, [year, month, fetchUsage, t]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center justify-between pr-6">
            <DialogTitle>{t("ui.usageReport.title")}</DialogTitle>
            <Select value={selectedMonth} onValueChange={setSelectedMonth}>
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {monthOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </DialogHeader>

        {loading && (
          <div className="py-10 text-center text-sm text-muted-foreground">
            {t("ui.usageReport.loading")}
          </div>
        )}

        {error && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {data && !loading && (
          <div className="space-y-6">
            <OverviewChart
              data={data}
              models={data.models}
              year={year}
              month={month}
              t={t}
            />
            {data.by_model.map((model, idx) => (
              <ModelSection
                key={model.model}
                model={model}
                colorIdx={idx}
                t={t}
              />
            ))}
            {data.by_model.length === 0 && (
              <div className="py-10 text-center text-sm text-muted-foreground">
                {t("ui.usageReport.noData")}
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
