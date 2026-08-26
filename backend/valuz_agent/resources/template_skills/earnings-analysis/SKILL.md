---
name: earnings-analysis
description: Post-earnings quarterly update reports for global companies under coverage (focus US / HK / A-shares, also other markets). Analyzes earnings flashes/formal filings, generates variance tables (actual vs consensus vs prior), flags key drivers, and drafts structured earnings notes in sell-side format. Pulls actual/historical financials, guidance, and indicator values via valuz-data, and retrieves earnings reports, calls, research, minutes, and filings via valuz-search. Triggers on "财报分析", "季度业绩点评", "年报/中报点评", "earnings review", or "[company] earnings".
---

# earnings-analysis

## Purpose

Create professional **季度/年度业绩点评报告**, analyzing results for global 上市公司（美股/港股/A 股为主，兼顾其他市场）already under coverage. Follow established sell-side research standards.

## Data Sources

### Tools

- **valuz-data** — 实际/历史财务、指引、指标数值数据.
- **valuz-search** — 财报、电话会、公告、纪要检索.

Use **valuz-data** to pull actual financials; use **valuz-search** to retrieve the underlying earnings reports, earnings-call transcripts, consensus research, and filings.

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.

```python
# 实际/历史财务 — valuz-data
get_financial_statements(statement_type="income", symbol="US:AAPL", period="quarterly", limit=8) → 利润表
get_financial_statements(statement_type="balance", symbol="US:AAPL", period="quarterly", limit=8) → 资产负债表
get_financial_statements(statement_type="cash_flow", symbol="US:AAPL", period="quarterly", limit=8) → 现金流量表
get_financial_statements(statement_type="revenue_breakdown", symbol="US:AAPL", period="quarterly") → 分业务/分地区收入
get_company(kind="profile", symbol="US:AAPL") → 公司概览
get_snapshots(symbol="US:AAPL") → 价格、涨跌、成交量
get_valuations(kind="latest", symbol="US:AAPL") → PE/PB/PS、市值
get_calendar(kind="earnings", symbol="US:AAPL") → 财报日历

# 财报原文 / 电话会 / 公告 / 一致预期 — valuz-search (market:ticker)
search_documents(category="earnings_reports", symbols="US:AAPL") → 交易所财报
search_documents(category="earnings_calls", symbols="US:AAPL") → 业绩电话会纪要/演示
search_documents(category="filings", symbols="US:AAPL") → 公告
search_documents(category="research_reports", symbols="US:AAPL") → 机构研报 / 一致预期
get_document_chunks(kind="list", document_id=...) → 取上述检索结果的可引用原文 chunk
```

### Secondary Sources (via valuz-search)

- **交易所财报 / 公告** — retrieve with `search_documents`, pull full text with `get_document(kind="raw_content")` (covers SEC EDGAR、港交所披露易、巨潮资讯等各市场披露平台)
- **业绩电话会 / 业绩说明会** — `search_documents` (纪要/演示), full text via `get_document(kind="raw_content")`
- **机构研报 / 一致预期** — `search_documents` (analyst consensus & sell-side views)

## Key Financial Terms (Chinese → English)

| Chinese | English | Notes |
|---------|---------|-------|
| 营业收入 | Revenue | Top-line, 按当地准则口径 |
| 营业成本 | COGS | Cost of goods sold |
| 毛利率 | Gross margin | 营业利润/营业收入 |
| 营业利润 | Operating profit | 核心经营利润 |
| 归母净利润 | Net income attributable to parent | Key coverage metric (US/HK: net income to shareholders) |
| 扣非净利润 | Net income (non-GAAP adj.) | Deducts one-time items; US/HK equivalent = adjusted / non-GAAP net income |
| 净利润 | Net income | May include minority interest |
| 经营活动现金流 | Operating CF | 经营现金流 |
| 资本支出 | CapEx | 购建固定资产 |
| 有息负债 | Interest-bearing debt | 有息负债 |
| 每股收益 (EPS) | Earnings per share | 基本EPS / 稀释EPS |

## Workflow

### Step 1: Pull the Earnings Print

**Data to collect:**
- Current quarter / full year income statement — actual via `get_financial_statements` (valuz-data); original filing via `search_documents` (valuz-search) + `get_document(kind="raw_content")` (valuz-data)
- Balance sheet and cash flow statement — `get_financial_statements` (valuz-data)
- Management commentary / 业绩说明会 / earnings-call transcript — `search_documents` (valuz-search) + `get_document(kind="raw_content")` (valuz-data)
- Company press release / 公告 — `search_documents` (valuz-search) + `get_document(kind="raw_content")` (valuz-data)

**Verify against:**
- Previous quarter guidance (管理层指引) — pull from prior 电话会 via `search_documents` + `get_document(kind="raw_content")`, compare to actuals
- Consensus estimates (一致预期) — `search_documents` (valuz-search) if available

### Step 2: Build the Variance Table

**Required columns:**

| Item | 实际 (Actual) | 一致预期 (Consensus) | 上次预测 (Prior) | 同比 (YoY) | 环比 (QoQ) | Surprise |
|------|---------------|---------------------|------------------|------------|------------|----------|
| 营业收入 | | | | | | |
| 毛利率 | | | | | | |
| 归母净利润 | | | | | | |
| EPS (基本) | | | | | | |
| 经营现金流 | | | | | | |

**Surprise calculation:**
- Beat/Miss % = (Actual - Consensus) / |Consensus| × 100%
- Flag:
  - **大幅超预期**: >+10%
  - **符合预期**: -10% to +10%
  - **低于预期**: <-10%

**Consensus sources:**
- 实际数用 `get_financial_statements` (valuz-data)；一致预期用 `search_documents` (valuz-search)；财报原文/电话会指引/公告用 `search_documents` (valuz-search) + `get_document(kind="raw_content")` (valuz-data)
- If unavailable, note `[UNSOURCED]`

### Step 3: Analyze Key Drivers

**Revenue:**
- Volume vs price decomposition
- Segment revenue breakdown (if disclosed) — `get_financial_statements` (valuz-data)
- New product / customer contribution
- Industry volume trends

**Margins:**
- 毛利率变化: input cost (原材料), pricing power, product mix
- 费用率变化: 费用率 = 费用 / 营业收入
- 营业利润率 trends

**Balance Sheet:**
- 应收账款增速 vs 收入增速
- 存货积压 risk
- 有息负债 changes
- 商誉 level (flag impairment risk)

**Cash Flow:**
- 经营现金流 vs 净利润 (quality of earnings)
- 资本支出 intensity
- Free cash flow = 经营CF - 资本支出

### Step 4: Update Estimates

**Forward estimates adjustment:**

Based on current quarter results and management guidance:

- Update FY20XXE revenue, margins, EPS
- Adjust Q2-Q4 quarterly estimates
- Update annual consensus if material change
- Flag if guidance was provided

**Estimate change table:**

| Item | Old Estimate | New Estimate | Change | Driver |
|------|-------------|--------------|--------|--------|
| FY Revenue | | | | |
| FY Net Income | | | | |
| FY EPS | | | | |

### Step 5: Valuation Update

**Current valuation metrics:**
- Current stock price + daily change — `get_snapshots` (valuz-data)
- PE (动 / 静), PB, PS — `get_snapshots` (valuz-data)
- EV/EBITDA (if applicable)
- 52-week high/low, YTD performance — `get_snapshots` (valuz-data)
- Relative to sector median

**Post-earnings re-rating assessment:**
- Did multiple expand/contract?
- Is valuation now cheap/expensive vs history and peers?
- 目标价 adjustment rationale

### Step 6: Draft the Report

**Standard earnings update structure:**

```
标题：[公司名称]（[代码]）[Q20XX / FY20XX] 业绩点评：[超预期/符合预期/低于预期]

一、业绩概览
  - 核心数据一览表
  - 同比/环比增速
  - 超出/低于一致预期幅度

二、收入分析
  - 收入增速分解
  - 分业务/分地区收入
  - 量价分析

三、利润分析
  - 毛利率变化及原因
  - 费用率分析
  - 净利润增速分解

四、资产负债表
  - 应收账款
  - 存货
  - 有息负债
  - 其他关注点

五、现金流量
  - 经营现金流
  - 资本支出
  - 自由现金流

六、估值与投资建议
  - 当前估值水平
  - 目标价调整
  - 评级维持/调整

七、风险提示
  - 行业政策风险
  - 原材料价格波动
  - 竞争加剧
  - 商誉减值风险
```

### Step 7: Quality Check

**Before delivering:**

- [ ] All numbers sourced from valuz-data (`get_financial_statements` / `get_snapshots`) or valuz-search (`search_documents` + `get_document(kind="raw_content")`)
- [ ] Variance table complete with actual/consensus/prior
- [ ] Beat/miss flagged with % surprise
- [ ] Key drivers identified and explained
- [ ] Forward estimates updated
- [ ] Valuation metrics current
- [ ] Risk factors listed (market-specific)
- [ ] Citations complete; unsourced items marked `[UNSOURCED]`

## Market-Specific Elements

### Earnings Call (业绩说明会 / earnings call)
- Schedule: typically same day or next trading day after earnings release
- Channels: 各市场披露平台与投资者问答（SEC EDGAR、港交所披露易、巨潮资讯、上证e互动 / 深交所互动易，及 IR sites）
- Format: online video/audio + text Q&A
- Key questions may come from analysts (US/HK) or retail investors (A-share)

### Regulatory Requirements
Filing cadence varies by market — confirm the relevant regime:
- A-share: 业绩预告 (earnings preview) required if variance >50%; 业绩快报 (earnings flash) optional, typically 10 days before full report; full annual report 4 months after year-end; semi-annual 2 months after H1; quarterly 1 month after quarter-end
- US: 10-K (annual) / 10-Q (quarterly) / 8-K (material events) per SEC deadlines
- HK: annual / interim reports per HKEX listing rules

### Accounting Nuances
- 扣非净利润 (non-GAAP / adjusted net income) widely used by analysts — US/HK report adjusted / non-GAAP equivalents
- 其他收益 (other income) can mask core operating performance
- 政府补助 (government subsidies) significant for some sectors
- 资产减值损失 (asset impairment) — flag if unexpectedly large
- 股份支付 (share-based compensation) — expensed immediately under CAS / IFRS / US GAAP

### Market Context
- Price-move limits differ by market (e.g. A-share ±10% main board, ±20% 创业板/科创板; US/HK have no daily limit)
- Institutional vs retail flow (e.g. A-share northbound 北向资金) may react strongly to earnings
- Earnings can cause outsized moves, especially in retail-heavy markets

## Source Citations

**Format for data citations:**

```
Source: valuz-search, search_documents(category="all", symbols=["US:AAPL"]) → get_document(kind="raw_content", document_id=...), [Company] 2024 年度报告 / annual report, p.[X], [URL if applicable]
Source: valuz-data, get_financial_statements(statement_type="income", symbol="US:AAPL", period="quarterly")
Source: valuz-search, search_documents(category="all", symbols=["US:AAPL"]) — 一致预期, accessed [Date]
Source: valuz-search, search_documents(category="all", symbols=["US:AAPL"]) → get_document(kind="raw_content", document_id=...) — 电话会/业绩说明会, accessed [Date]
```

**Symbols across markets:** valuz-data MARKET:LOCAL 规范代码 US `AAPL` / HK `00700` / A-share `600519`；valuz-search `market:ticker` US `US:AAPL` / HK `HK:00700` / A-share `SH:600519`.

**For figures not from primary sources:**
```
[UNSOURCED] — estimate based on [rationale]
```
