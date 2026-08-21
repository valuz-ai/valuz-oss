---
name: pptx-author
description: >
  Generic PowerPoint authoring skill for global equity investment analysis and pitch decks.
  Creates professional 路演PPT / 投资分析PPT for any listed company across global stock markets
  (US / HK / A-share focus, other markets too) using live data from the `valuz-data` connector
  (quotes, financials, indicators) and the `valuz-search` connector (filings, research). All
  company-specific values are parameterized — never hardcoded.
  Triggers on "股票PPT制作", "投资PPT", "制作PPT", "路演PPT", "pitch deck",
  "PowerPoint [company/ticker]", or any request to create slides for a listed stock.
  When invoked, use the `generate_equity_ppt` script with --company and --ticker args.
---

# pptx-author

## Purpose

Generate professional **投资分析PPT (equity investment decks)** for any listed company.
This skill is a generic engine — every output is driven by two parameters:

| Parameter | Example | Description |
|-----------|---------|-------------|
| `{{COMPANY_NAME}}` | {{COMPANY_NAME}} | Full company name (e.g., Apple Inc. / 腾讯控股 / 贵州茅台) |
| `{{TICKER}}` | {{TICKER}} | Bare ticker for `valuz-data` (e.g., AAPL / 00700 / 600519) |
| `{{MARKET}}` | {{MARKET}} | Market prefix for `valuz-search` symbols (US / HK / SH) |
| `{{OUTPUT_PATH}}` | ./output.pptx | Where to save the PPTX file |

All financial figures, price data, peer valuations, and company descriptions are
fetched live from `valuz-data` / `valuz-search` connectors. **Nothing is hardcoded.**

---

## Data Pipeline

Most deck content comes from team output; the data connectors are used only to
fill in charts and figures. Two connectors back the data slides:

- **`valuz-data`** — quantitative data: quotes, financial statement values, segments.
- **`valuz-search`** — qualitative data: filings, research, for citation/佐证.

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.
> `valuz-search` takes a `market:ticker` symbol (US:AAPL / HK:00700 / SH:600519).

### Step 1: Resolve Company Info

```python
# valuz-data — get_company (canonical MARKET:LOCAL symbol)
get_company(kind="profile", symbol="{{TICKER}}")
# → company profile + identifiers (e.g. NASDAQ / HKEX / SSE), full legal name
```

```python
# valuz-data — get_snapshots (canonical MARKET:LOCAL symbol)
get_snapshots(symbol="{{TICKER}}")
# → live price, market cap, turnover (for cover / highlights)
```

```python
# valuz-data — financial statements (canonical MARKET:LOCAL symbol)
get_financial_statements(statement_type="income", symbol="{{TICKER}}", period="annual", limit=5)
# → annual income statement: revenue, net profit, margins, EPS, etc.
get_financial_statements(statement_type="balance", symbol="{{TICKER}}", period="annual", limit=5)
# → balance sheet: assets, liabilities, equity
get_financial_statements(statement_type="cash_flow", symbol="{{TICKER}}", period="annual", limit=5)
# → cash flow statement
get_financial_statements(statement_type="revenue_breakdown", symbol="{{TICKER}}", period="annual", limit=5)
# → segment / revenue mix for operating-metrics charts
```

```python
# valuz-data — price history for trend charts (canonical MARKET:LOCAL symbol)
get_bars(kind="bars", symbol="{{TICKER}}", limit=260)
# → OHLCV price history; use get_bars(kind="bars", symbol=...) for candlestick rendering
```

```python
# valuz-search — qualitative material for citations (market:ticker symbol)
search_documents(category="all", query="{{COMPANY_NAME}} business overview", symbols=["{{MARKET}}:{{TICKER}}"])
search_documents(category="all", query="{{COMPANY_NAME}} annual report", symbols=["{{MARKET}}:{{TICKER}}"])
# → research notes / filings to cite and support narrative slides
```

### Step 2: Peer / Comps Data

```python
# Pull peer figures with the same valuz-data calls (get_snapshots / get_financial_statements)
# on each comparable ticker; focus on top 5-8 comps by revenue/market cap.
```

### Step 3: Generate Charts

Use matplotlib to create:
1. **Revenue & Profit trend** — from `get_financial_statements` (valuz-data)
2. **Margin trends** — gross margin, net margin, ROE over time (`get_financial_statements`)
3. **Growth rates** — YoY revenue and profit growth (`get_financial_statements`)
4. **Peer comparison** — horizontal bar chart of PE and PB vs peers (`get_snapshots`)
5. **Price trend** — from `get_bars` / `get_bars` (valuz-data)
6. **Segment mix** — from `get_financial_statements` (valuz-data)

All chart titles and labels must include `{{COMPANY_NAME}}` dynamically.

---

## Presentation Structure

The default deck is **12 slides**. Adjust sections based on `deck_type` parameter.

### Template (parameterized)

```
Slide 1:  Cover
    {{COMPANY_NAME}} ({{TICKER}})
    {{REPORT_TITLE}}  |  {{DATE}}
    机密文件 | 仅供内部参考

Slide 2:  投资摘要 (Investment Highlights)
    • {{HIGHLIGHT_1}}
    • {{HIGHLIGHT_2}}
    • {{HIGHLIGHT_3}}
    目标价: {{CURRENCY}}{{TARGET_PRICE_LOW}} - {{CURRENCY}}{{TARGET_PRICE_HIGH}}
    评级: {{RATING}}

Slide 3:  公司概览 (Company Overview)
    {{COMPANY_DESCRIPTION}}
    主营业务: {{MAIN_BUSINESS}}
    成立时间 | 上市市场 | 控股股东 (from get_company / get_snapshots / valuz-search)

Slide 4:  行业分析 (Industry Overview)
    {{INDUSTRY_NAME}} — market size, trends, policy
    Data from: search_documents(category="all") (valuz-search) + peer figures

Slide 5:  财务分析 (Financial Summary)
    [Chart: Revenue & Profit]  [Chart: Margin Trends]
    [Chart: Growth Rates]
    All data from get_financial_statements(statement_type=..., period="annual") (valuz-data)

Slide 6:  运营指标 (Operating Metrics)
    Segment breakdown, KPIs (if available from financials)

Slide 7:  可比公司估值 (Trading Comparables)
    [Chart: Peer PE/PB comparison]
    Table: Peer | Market Cap | P/E | P/B | EV/EBITDA

Slide 8:  估值分析 (Valuation)
    [Chart: Football field]
    DCF / PE / PB ranges derived from peer analysis

Slide 9:  投资逻辑 (Investment Thesis)
    3-5 key investment bullets derived from data analysis

Slide 10: 风险提示 (Risk Factors)
    Generic risk categories, customized from industry context

Slide 11: 投资建议 (Recommendation)
    评级 + 目标价 range + upside calculation

Slide 12: 免责声明 (Disclaimer)
    Standard equity research disclaimer
```

### Deck Type Variants

| deck_type | Slides | Audience | Key sections |
|-----------|--------|----------|--------------|
| `pitch` | 12 | Clients/Investors | Full deck above |
| `deep_dive` | 20-30 | Internal IC | Add: detailed financial model, sensitivity, scenario analysis |
| `initiation` | 15-20 | Internal | Emphasize: company overview, industry, thesis |
| `sector` | 10-15 | Internal | Emphasize: industry analysis, peer comparison |
| `morning_note` | 5-8 | Internal | Condensed: key data, thesis, recommendation |

---

## Design Standards

| Element | Standard |
|---------|----------|
| 模板 | Corporate blue/gold theme (configurable) |
| 字体 | 微软雅黑 / system default sans-serif |
| 字号 | Title 24-32pt, Body 12-16pt |
| 配色 | Primary: #1A3C6E (deep blue), Accent: #C8A032 (gold) |
| 每页标题 | Chinese + English bilingual |
| 数据来源 | Cite at bottom of each data slide |
| 页码 | Bottom center |
| 免责声明 | Last slide, mandatory |

---

## Content Rules

| Rule | Guideline |
|------|-----------|
| 每页一个主题 | One idea per slide |
| 标题先行 | Clear headline at top of every slide |
| 数据可视化 | Charts > tables > text |
| 双语呈现 | Primary language Chinese, with English subtitles (adapt to target market) |
| 术语统一 | Use standard equity-research terminology (see below) |
| 不编造数据 | If data unavailable from connector, show "N/A" or omit |

### Equity-Research Terminology

| English | Chinese |
|---------|---------|
| Revenue | 营业收入 |
| Net income (attributable) | 归母净利润 |
| Gross margin | 毛利率 |
| Net margin | 净利率 |
| ROE | ROE (净资产收益率) |
| EPS | 每股收益 |
| P/E | 市盈率 |
| P/B | 市净率 |
| Target price | 目标价 |
| Rating | 评级 |
| Upside | 上行空间 |
| YoY | 同比 |
| QoQ | 环比 |
| Market cap | 总市值 |
| Turnover | 换手率 |

---

## Quality Checklist

Before delivering the PPT:
- [ ] All `{{PLACEHOLDER}}` replaced with live data
- [ ] Charts readable, labeled, sourced
- [ ] Numbers consistent across slides (revenue matches across chart/table/text)
- [ ] Company name and ticker correct throughout
- [ ] Peer data reflects actual industry comparables
- [ ] Disclaimer slide included
- [ ] File named: `{{COMPANY_NAME}}_{{TICKER}}_{{REPORT_TITLE}}.pptx`

---

## Usage

Invoke the generation script (from project root):

```bash
cd {{PROJECT_ROOT}} && python3 scripts/generate_equity_ppt.py \
  --company "{{COMPANY_NAME}}" \
  --ticker "{{TICKER}}" \
  --industry "{{INDUSTRY_NAME}}" \
  --output "{{OUTPUT_PATH}}" \
  --type {{DECK_TYPE}} \
  --title "{{REPORT_TITLE}}"
```

Required args: `--company`, `--ticker`
Optional: `--industry` (for peer lookup), `--output`, `--type` (pitch/deep_dive/initiation), `--title`

If `--industry` is omitted, the script attempts to infer it from the company's sector classification.

Reference report/deck styles after major global research houses — e.g. 高盛/摩根士丹利/中金 等
(Goldman Sachs / Morgan Stanley / CICC) — adapting the house style to the target market.

Examples across markets:
- US: `--company "Apple Inc." --ticker "AAPL"`
- HK: `--company "腾讯控股" --ticker "0700.HK"`
- A-share: `--company "贵州茅台" --ticker "600519.SH"`

All quantitative data flows from the `valuz-data` connector (行情/财务数值); all qualitative
material flows from the `valuz-search` connector (定性资料). No vendor API keys to configure —
the connectors are provided by the Valuz workstation.
