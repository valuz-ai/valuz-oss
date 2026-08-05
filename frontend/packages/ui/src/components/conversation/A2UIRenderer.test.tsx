import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@openuidev/react-ui", () => ({
  AreaChartCondensed: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="area-chart">{JSON.stringify(data)}</div>
  ),
  BarChartCondensed: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="bar-chart">{JSON.stringify(data)}</div>
  ),
  Button: ({ children }: { children: ReactNode }) => <button>{children}</button>,
  Buttons: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Callout: ({
    title,
    description,
  }: {
    title?: ReactNode;
    description?: ReactNode;
  }) => (
    <aside>
      {title}
      {description}
    </aside>
  ),
  Card: ({
    children,
    variant,
  }: {
    children: ReactNode;
    variant?: string;
  }) => (
    <section data-testid="card" data-variant={variant ?? "card"}>
      {children}
    </section>
  ),
  CardHeader: ({
    title,
    subtitle,
  }: {
    title?: ReactNode;
    subtitle?: ReactNode;
  }) => (
    <header>
      <h2>{title}</h2>
      <p>{subtitle}</p>
    </header>
  ),
  CodeBlock: ({ codeString }: { codeString: string }) => <code>{codeString}</code>,
  FormControl: ({ children }: { children: ReactNode }) => <label>{children}</label>,
  HorizontalBarChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="horizontal-chart">{JSON.stringify(data)}</div>
  ),
  Image: ({ src, alt }: { src: string; alt?: string }) => (
    <img src={src} alt={alt} />
  ),
  ImageBlock: ({ src, alt }: { src: string; alt?: string }) => (
    <img src={src} alt={alt} />
  ),
  ImageGallery: ({
    images,
  }: {
    images: { src: string; alt?: string }[];
  }) => (
    <div>
      {images.map((image) => (
        <img key={image.src} src={image.src} alt={image.alt} />
      ))}
    </div>
  ),
  Input: (props: { placeholder?: string }) => <input {...props} />,
  Label: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  LineChartCondensed: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="line-chart">{JSON.stringify(data)}</div>
  ),
  MarkDownRenderer: ({ textMarkdown }: { textMarkdown: string }) => (
    <div>{textMarkdown}</div>
  ),
  PieChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="pie-chart">{JSON.stringify(data)}</div>
  ),
  RadarChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="radar-chart">{JSON.stringify(data)}</div>
  ),
  RadialChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="radial-chart">{JSON.stringify(data)}</div>
  ),
  ScatterChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="scatter-chart">{JSON.stringify(data)}</div>
  ),
  ScrollableTable: ({ children }: { children: ReactNode }) => (
    <table>{children}</table>
  ),
  Select: ({ children }: { children: ReactNode }) => <select>{children}</select>,
  SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectItem: ({
    children,
    value,
  }: {
    children: ReactNode;
    value: string;
  }) => <option value={value}>{children}</option>,
  SelectTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectValue: ({ placeholder }: { placeholder?: string }) => <>{placeholder}</>,
  Separator: () => <hr />,
  SingleStackedBar: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="stacked-chart">{JSON.stringify(data)}</div>
  ),
  SliderBlock: ({ label }: { label: string }) => <div>{label}</div>,
  TableBody: ({ children }: { children: ReactNode }) => <tbody>{children}</tbody>,
  TableCell: ({ children }: { children: ReactNode }) => <td>{children}</td>,
  TableHead: ({ children }: { children: ReactNode }) => <th>{children}</th>,
  TableHeader: ({ children }: { children: ReactNode }) => <thead>{children}</thead>,
  TableRow: ({ children }: { children: ReactNode }) => <tr>{children}</tr>,
  Tag: ({ text }: { text: ReactNode }) => <span>{text}</span>,
  TagBlock: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TextArea: (props: { placeholder?: string }) => <textarea {...props} />,
  TextCallout: ({
    title,
    description,
  }: {
    title?: ReactNode;
    description?: ReactNode;
  }) => (
    <aside>
      {title}
      {description}
    </aside>
  ),
  TextContent: ({
    children,
    variant,
  }: {
    children: ReactNode;
    variant?: string;
  }) => (
    <p data-testid="text-content" data-variant={variant ?? "sunk"}>
      {children}
    </p>
  ),
}));

vi.mock("@openuidev/react-ui/Modal", () => ({
  Modal: ({ children, title }: { children: ReactNode; title: string }) => (
    <div role="dialog" aria-label={title}>
      {children}
    </div>
  ),
}));

import { A2UIRenderer } from "./A2UIRenderer";

describe("A2UIRenderer", () => {
  it("renders A2UI v0.9 streams with the OpenUI component mapping", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "dashboard", catalogId: "openui" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "dashboard",
          components: [
            {
              id: "root",
              component: "Stack",
              children: ["header", "metric", "table", "chart"],
            },
            {
              id: "header",
              component: "CardHeader",
              title: "Catalog coverage",
              subtitle: "OpenUI aliases",
            },
            {
              id: "metric",
              component: "Metric",
              label: "Revenue",
              value: "$12.4M",
            },
            {
              id: "table",
              component: "Table",
              columns: [
                { component: "Col", label: "Region", data: ["North"] },
                { component: "Col", label: "Revenue", data: [12] },
              ],
            },
            {
              id: "chart",
              component: "BarChart",
              labels: ["Q1", "Q2"],
              series: [{ component: "Series", category: "Revenue", values: [10, 12] }],
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(<A2UIRenderer body={messages} />);

    expect(screen.getByText("Catalog coverage")).toBeTruthy();
    expect(screen.getAllByText("Revenue")).toHaveLength(2);
    expect(screen.getByText("$12.4M")).toBeTruthy();
    expect(screen.getByText("Region")).toBeTruthy();
    expect(screen.getByText("North")).toBeTruthy();
    expect(screen.getByTestId("bar-chart").textContent).toContain("Q1");
  });

  it("accepts legacy nested props and inline child component objects", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "legacy", catalogId: "valuz" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "legacy",
          components: [
            {
              id: "root",
              component: "Stack",
              props: { direction: "column" },
              children: [
                {
                  component: "TextContent",
                  props: { text: "Legacy payload" },
                },
              ],
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(<A2UIRenderer body={messages} />);

    expect(screen.getByText("Legacy payload")).toBeTruthy();
  });

  it("infers the active surface when adding a missing root component", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "custom-surface", catalogId: "openui" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "custom-surface",
          components: [
            {
              id: "summary",
              component: "TextContent",
              text: "Rendered without an explicit root",
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(<A2UIRenderer body={messages} />);

    expect(screen.getByText("Rendered without an explicit root")).toBeTruthy();
  });

  it("renders dashboard-style A2UI payloads without turning every text item into a card", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "main", catalogId: "openui" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "main",
          components: [
            {
              id: "root",
              component: "Stack",
              direction: "column",
              children: ["title", "indexSection", "chart", "breadth"],
            },
            {
              id: "title",
              component: "TextContent",
              text: "A股 · 大盘与板块行情看板",
            },
            {
              id: "indexSection",
              component: "Section",
              title: "主要指数",
              children: ["indexCard"],
            },
            {
              id: "indexCard",
              component: "Card",
              children: ["indexName", "latest", "pct"],
            },
            {
              id: "indexName",
              component: "TextContent",
              text: "上证指数 000001",
              size: "small",
            },
            {
              id: "latest",
              component: "KPI",
              label: "最新点位",
              value: "3,830.84",
            },
            {
              id: "pct",
              component: "TextContent",
              text: "+0.56%",
              size: "medium-heavy",
            },
            {
              id: "chart",
              component: "HorizontalBarChart",
              children: [
                {
                  component: "Series",
                  name: "涨跌幅（%）",
                  data: [
                    { name: "创业板指", value: 5.73 },
                    { name: "上证指数", value: 0.56 },
                  ],
                },
              ],
            },
            {
              id: "breadth",
              component: "PieChart",
              children: [
                { component: "Slice", name: "上涨", value: 1422 },
                { component: "Slice", name: "下跌", value: 862 },
              ],
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(<A2UIRenderer body={messages} />);

    const sectionTitle = screen.getByText("主要指数");
    expect(sectionTitle.closest("[data-a2ui-component='section']")).toBeTruthy();
    expect(sectionTitle.closest("[data-testid='card']")).toBeNull();
    expect(
      screen
        .getByText("A股 · 大盘与板块行情看板")
        .closest("[data-testid='text-content']")
        ?.getAttribute("data-variant"),
    ).toBe("clear");
    const metric = screen
      .getByText("最新点位")
      .closest("[data-a2ui-component='metric']");
    expect(metric?.textContent).toContain("最新点位");
    expect(metric?.textContent).toContain("3,830.84");
    expect(metric?.querySelector("[data-a2ui-metric-label]")?.textContent).toBe(
      "最新点位",
    );
    expect(metric?.querySelector("[data-a2ui-metric-value]")?.textContent).toBe(
      "3,830.84",
    );
    const indexCard = screen
      .getByText("上证指数 000001")
      .closest("[data-testid='card']");
    expect(indexCard?.querySelector("[data-a2ui-card-content]")).toBeTruthy();
    expect(
      screen
        .getByText("上证指数 000001")
        .closest("[data-a2ui-text-size='small']"),
    ).toBeTruthy();
    expect(
      screen
        .getByText("+0.56%")
        .closest("[data-a2ui-text-size='medium-heavy']"),
    ).toBeTruthy();
    expect(screen.getByTestId("horizontal-chart").textContent).toContain("创业板指");
    expect(screen.getByTestId("pie-chart").textContent).toContain("上涨");
  });

  it("renders semantic finance components with structured dashboard markup", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "finance", catalogId: "openui" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "finance",
          components: [
            {
              id: "root",
              component: "Stack",
              direction: "column",
              children: ["indices", "pe", "ranking", "emptyChart", "breadth"],
            },
            {
              id: "indices",
              component: "MarketIndexGrid",
              title: "主要指数",
              description: "实时行情",
              indices: [
                {
                  name: "上证指数",
                  code: "000001",
                  latest: "3,830.84",
                  change: "+21.18",
                  changePct: "+0.56%",
                  turnover: "7,908.59亿",
                },
                {
                  name: "创业板指",
                  code: "399006",
                  latest: "3,491.63",
                  change: "+189.08",
                  change_pct: "+5.73%",
                  turnover: "4,803.02亿",
                },
              ],
            },
            {
              id: "pe",
              component: "FinanceMetric",
              label: "TTM市盈率",
              value: 76.1,
              unit: "倍",
              changePct: "+3.2%",
              description: "当前值",
            },
            {
              id: "ranking",
              component: "DataList",
              title: "行业板块涨幅排行",
              description: "按涨跌幅排序",
              items: [
                {
                  rank: 1,
                  name: "其他数字媒体",
                  value: "924.24",
                  changePct: "+8.77%",
                },
                {
                  rank: 2,
                  name: "医疗研发外包",
                  value: "7352.92",
                  change_pct: "+8.06%",
                },
              ],
            },
            {
              id: "emptyChart",
              component: "LineChart",
              title: "空图不应该渲染",
            },
            {
              id: "breadth",
              component: "MarketBreadth",
              up: 1422,
              down: 862,
              flat: 66,
              source: "东方财富",
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    const { container } = render(<A2UIRenderer body={messages} />);

    const grid = container.querySelector('[data-a2ui-component="market-index-grid"]');
    expect(grid).toBeTruthy();
    expect(screen.getByText("主要指数")).toBeTruthy();
    expect(screen.getByText("实时行情")).toBeTruthy();
    expect(
      container.querySelectorAll('[data-a2ui-component="market-index-card"]'),
    ).toHaveLength(2);
    expect(screen.getByText("上证指数")).toBeTruthy();
    expect(screen.getByText("000001")).toBeTruthy();
    expect(
      screen.getByText("3,830.84").closest("[data-a2ui-market-index-value]"),
    ).toBeTruthy();
    expect(
      screen.getByText("+0.56%").closest("[data-a2ui-market-index-change]"),
    ).toBeTruthy();
    expect(screen.getByText("成交额 7,908.59亿")).toBeTruthy();
    expect(screen.getByText("创业板指")).toBeTruthy();
    expect(screen.getByText("+5.73%")).toBeTruthy();

    // FinanceMetric's bespoke renderer was retired: the name now resolves onto
    // the StatsCard block, which carries the same label/value/unit/change
    // shape. The payload is unchanged, so the rendered content must be too.
    const financeMetric = container.querySelector('[data-slot="vgb-stats-card"]');
    expect(financeMetric?.textContent).toContain("TTM市盈率");
    expect(financeMetric?.textContent).toContain("76.1");
    expect(financeMetric?.textContent).toContain("倍");
    expect(financeMetric?.textContent).toContain("+3.2%");

    const ranking = container.querySelector(
      '[data-a2ui-component="data-list"]',
    );
    expect(ranking?.textContent).toContain("行业板块涨幅排行");
    expect(ranking?.textContent).toContain("1");
    expect(ranking?.textContent).toContain("其他数字媒体");
    expect(ranking?.textContent).toContain("924.24");
    expect(ranking?.textContent).toContain("+8.77%");
    expect(ranking?.textContent).toContain("医疗研发外包");
    expect(
      container.querySelectorAll("[data-a2ui-data-list-row]"),
    ).toHaveLength(2);
    expect(screen.queryByTestId("line-chart")).toBeNull();

    const breadth = container.querySelector('[data-a2ui-component="market-breadth"]');
    expect(breadth?.textContent).toContain("上涨 1,422");
    expect(breadth?.textContent).toContain("下跌 862");
    expect(breadth?.textContent).toContain("平盘 66");
    expect(breadth?.textContent).toContain("东方财富");
  });
});
