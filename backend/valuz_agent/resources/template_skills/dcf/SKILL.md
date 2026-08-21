---
name: dcf
description: DCF valuation model for global equities (US / HK / A-shares focus, also other markets) using Valuz financial data. Uses valuz-data (Valuz Quotes MCP — real-time & historical quotes, financial statements, indicators) for financials and WACC inputs (risk-free rate from the relevant market's government bond yields) and valuz-search (Valuz Search MCP — earnings reports, calls, research, minutes, filings) for qualitative context and growth projections. Use instead of the original dcf-model skill for cross-market equities.
---

# dcf

## Data Sources

全球股票市场（美股/港股/A 股为主，兼顾其他市场）的 DCF 建模，统一使用两个 Valuz 连接器取数：

- `valuz-data` (Valuz Quotes MCP) — 行情、财务三表、指标、营收拆分等数值数据（quantitative/numeric）。
- `valuz-search` (Valuz Search MCP) — 财报、公告、研报、纪要、电话会、新闻检索（qualitative/text）。

> **代码格式**：两个连接器都使用 `MARKET:LOCAL` 规范代码（`US:AAPL` / `HK:00700` / `SH:600519`）；非规范输入先调用 `resolve_symbols`。

> 取数原则：用 `get_financial_statements` / `get_snapshots` / `get_bars` / `get_financial_statements`（valuz-data）取财务与行情数据；用 `search_documents`（valuz-search）取财报、纪要、研报、公告。

```text
get_financial_statements(statement_type="income", symbol, period="annual")    -> Historical P and L
get_financial_statements(statement_type="balance", symbol, period="annual")   -> Historical BS
get_financial_statements(statement_type="cash_flow", symbol, period="annual") -> Historical CF
get_snapshots(symbol)                              -> Current price
get_valuations(kind="latest", symbol)             -> Market cap and multiples
get_financial_statements(statement_type="revenue_breakdown", symbol) -> Revenue drivers
search_documents(category="research_reports", query=..., symbols=[...]) -> Research / guidance
```

## Key differences across markets

DCF conventions vary by the stock's home market — pick parameters per the standard
that applies to the ticker (US / HK / A-shares / other), not a single fixed market.

| Parameter | US DCF Convention | A-share DCF Convention |
|-----------|-------------------|---------------------|
| Risk-free rate | US 10Y Treasury | China 10Y CGB (国债收益率, ~2.5-3.5%) |
| Equity risk premium | ~5-6% (historical US) | ~6-8% (China A-share ERP) |
| Tax rate | US corporate 21% | China corporate 25% (高新技术企业 15%) |
| Terminal growth | US GDP growth (~2%) | China GDP growth (~4-5%) |
| Currency | USD | CNY |
| Reporting standard | US GAAP / IFRS | CAS (中国会计准则) |

> 实际建模时，按标的适用准则（US GAAP / IFRS / CAS）读取报表口径，营收等按当地准则口径（如增值税处理差异）处理。

## Workflow

### Step 1: Pull financials

用 `get_financial_statements`（valuz-data，`period="annual"`，`limit` 取近 5 年；季度建基期改 `period="quarterly"`）拉历史三表：

```text
get_financial_statements(statement_type="income", symbol, period="annual", limit=5)
get_financial_statements(statement_type="balance", symbol, period="annual", limit=5)
get_financial_statements(statement_type="cash_flow", symbol, period="annual", limit=5)
```

> `valuz-data` 使用 `US:AAPL`、`HK:00700`、`SH:600519` 等规范代码。营收驱动可叠加 `get_financial_statements(statement_type="revenue_breakdown", symbol=...)`。

### Step 2: Get market data

```text
get_snapshots(symbol)                   → current price                  (valuz-data)
get_valuations(kind="latest", symbol)  → market cap, PE, PB              (valuz-data)
get_bars(kind="bars", symbol)          → 个股历史价格序列（算 β / 收益率） (valuz-data)
get_snapshots(index_symbol)            → benchmark 行情（β 估计基准）     (valuz-data)
```

用 `get_bars(kind="bars")` 分别取得个股与基准指数历史价格序列做 β 回归；基准指数按标的所在市场选。

### Step 3: Build projections

- Project revenue using historical growth rates adjusted for the relevant market's macro outlook（必要时用 `get_financial_statements`（valuz-data）分部驱动）
- Assume 65-75% operating margin for high-margin sectors (e.g. 白酒 / premium brands)
- Assume 15-25% operating margin for manufacturing
- CapEx as % of revenue: check historical from `get_financial_statements`（valuz-data）
- 用 `search_documents`（valuz-search，`query` 必填，`symbols=["US:AAPL"]` 等限定标的）拉财报、业绩电话会纪要与机构研报，校验前瞻指引（forward guidance）与增长假设

### Step 4: Compute WACC

```
WACC = E/(D+E) * Ke + D/(D+E) * Kd * (1 - tax_rate)

Ke = Rf + β * ERP
  Rf   = 对应市场国债（如美债/中债）10Y yield —— 无专门国债工具，用 search_documents(category="all")（valuz-search，query 必填，如 query="US 10Y Treasury yield"）查最新值，或作为分析师输入
  β    = regression on the market's benchmark index returns（get_bars vs get_snapshots, valuz-data）（or use comparable firm beta）
  ERP  = market-specific equity risk premium（同样可用 search_documents(category="all") 佐证，e.g. ~5-6% US, ~6-8% A-share）

Kd   = market 5Y corporate bond yield + credit spread（用 search_documents(category="all") 查对应市场公司债收益率）
```

### Step 5: Terminal value

```
Terminal Value = FCF_(n+1) / (WACC - g)
g = mature-company nominal GDP growth for the ticker's market (e.g. ~2% US, ~3-4% A-share)
```

## Notes

- Fiscal year-end varies by company/market (December 31 for most A-share/US names) — confirm per ticker
- Flow/sentiment indicators can be used as context (e.g. 北向资金 / Northbound flow for A-shares)
- 商誉 (goodwill) impairments are common in M&A — flag if goodwill > 30% of equity
- Watch reported units (e.g. 千元 thousands vs 元, or thousands/millions in USD statements) — check the unit
