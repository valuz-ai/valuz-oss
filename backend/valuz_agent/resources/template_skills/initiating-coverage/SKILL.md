---
name: initiating-coverage
description: Institutional-quality equity research initiation reports for companies across global stock markets (US / HK / A-shares focus, also other markets). Covers company analysis, financial modeling, valuation, chart preparation, and final report assembly. Uses the valuz-data connector for quotes, financials, and valuation indicators, and the valuz-search connector for earnings reports, calls, research, minutes, and filings. Triggers on "首次覆盖", "首次评级", "initiate coverage", "initiate on [company]", "研究报告", or "start research on [company]".
---

# initiating-coverage

## Purpose

Create institutional-quality **首次覆盖研究报告 (initiation reports)** for companies across global stock markets (美股/港股/A 股为主，兼顾其他市场), following mainstream sell-side research standards.

## Data Sources

首次覆盖以**整合团队成员产出**（行业研究、财务建模、跟踪覆盖）为主，仅在数据缺口处直接取数。取数走两个 Valuz 连接器。

> **代码格式**：两个连接器都使用规范的 `MARKET:LOCAL` 代码（`US:AAPL` / `HK:00700` / `SH:600519`）；名称、别名或非规范代码先交给 `resolve_symbols`。

### `valuz-data` — Valuz Data MCP（公司画像 / 财务 / 估值倍数 / 同业）

```text
get_company(kind="profile", symbol)          → 公司画像、规模、业务描述
get_ownership(kind="shareholders", symbol)  → 股东构成 / 股权结构
get_financial_statements(statement_type="income", symbol, period="annual")   → 历史损益
get_financial_statements(statement_type="balance", symbol, period="annual")  → 历史资产负债
get_financial_statements(statement_type="cash_flow", symbol, period="annual") → 历史现金流
get_industries(kind="constituents", ...)     → 同业（行业成分股）做可比集
compute_factors(symbols=[...], ...)               → PE()/PB()/PS()/EV-EBITDA 等估值倍数
get_snapshots(symbol)                              → 当前价格
get_valuations(kind="latest", symbol)             → 市值与当前估值倍数
```

### `valuz-search` — Valuz Search MCP（既有研报 / 公告 / 电话会 / 纪要）

```text
search_documents(category="research_reports", query=..., symbols=["US:AAPL", ...]) → 既有券商研报 / 行业观点
search_documents(category="earnings_calls", query=..., symbols=[...])    → 电话会 / 业绩会观点
search_documents(category="filings", query=..., symbols=[...])          → 年报、招股书等正式公告
search_documents(category="all", query=...)                       → 综合检索（财报/纪要/公告/新闻）
```

> **取数缺口对照**：公司画像 / 股东用 `get_company`、`get_ownership`；同业与估值倍数用 `get_industries`、`compute_factors` / `get_valuations`；既有研报 / 行业观点用 `search_documents(category="research_reports")`；财务佐证用 `get_financial_statements`；正式公告用 `search_documents(category="filings")`。

### Secondary Sources

- **search_documents**（valuz-search）— official filings（年报、招股书、上市文件、公告）for the target market
- **公司官网** — investor relations, presentations
- **Sell-side consensus** — consensus estimates
- **既有研报 / 行业观点** — existing analyst coverage, via `search_documents`（valuz-search）
- **行业协会** — industry data

## Workflow

### Task 1: Company Research

**Company overview:**（缺口数据：公司画像用 `get_company`(valuz-data，MARKET:LOCAL 规范代码)；股权 / 股东用 `get_ownership`(valuz-data)）
- Business description (主营业务)
- History and development (发展历程)
- Ownership structure (股权结构)
- Management team (管理层)
- Shareholder composition (股东构成)

**Business segments:**

| Segment | Revenue % | Margin | Growth Driver |
|---------|-----------|--------|---------------|
| | | | |

**Key questions to answer:**
1. What does the company do? How does it make money?
2. What is its competitive advantage? (护城河)
3. What are the key growth drivers?
4. What are the main risks?
5. Who are the comparable companies?（同业集用 `get_industries`(valuz-data)；既有行业观点用 `search_documents`(valuz-search，`market:ticker`)）

### Task 2: Financial Modeling

Build a financial model (refer to `3-statement-model` skill). 历史财务佐证取自 `get_financial_statements`(valuz-data，MARKET:LOCAL 规范代码，`period="annual"`)：

**Historical analysis (3-5 years):**

| Metric | 2020 | 2021 | 2022 | 2023 | 2024E | 2025E | 2026E |
|--------|------|------|------|------|-------|-------|-------|
| Revenue | | | | | | | |
| YoY Growth | | | | | | | |
| Gross Margin | | | | | | | |
| Operating Margin | | | | | | | |
| Net Margin | | | | | | | |
| ROE | | | | | | | |
| Net Debt/EBITDA | | | | | | | |

**Key modeling considerations (see 3-statement-model):**
- Accounting standards of the target market (US GAAP / IFRS / CAS as applicable)
- Local statutory tax rate (and any preferential rates that apply)
- Unit normalization (reporting currency and scale)
- Indirect/sales tax treatment
- Goodwill flagging
- R&D expense treatment

### Task 3: Valuation Analysis

**Build comprehensive valuation:**

1. **DCF** (refer to `dcf` skill)
   - WACC with the target market's risk-free rate
   - Market-appropriate equity risk premium (ERP)
   - Local statutory tax rate
   - Reporting-currency denominated

2. **Comparable companies** (refer to `comps` skill)
   - Peer set from the same industry classification（用 `get_industries`(valuz-data) 取同业）
   - PE, PB, PS, EV/EBITDA multiples（用 `compute_factors`(valuz-data) 计算倍数）
   - Regression analysis if >5 peers

3. **Precedent transactions** (if available)
   - Comparable M&A transaction multiples
   - Control premium analysis

4. **Sum-of-the-parts** (if multi-segment)
   - Segment-level DCF or multiples
   - Conglomerate discount assessment

**Valuation summary:**

| Method | Value (per share) | Weight | Rationale |
|--------|-------------------|--------|-----------|
| DCF | | | |
| PE comps | | | |
| PB comps | | | |
| EV/EBITDA comps | | | |
| SOTP | | | |
| **Implied target** | | | |

### Task 4: Chart Preparation

**Required charts:**

1. **Revenue & earnings history**
   - 5-year historical + 3-year projected
   - Bar chart: Revenue
   - Overlay: Net income, margins

2. **Valuation multiple history**
   - PE band (5-year)
   - PB band (5-year)
   - Current vs historical average

3. **Peer comparison**
   - Scatter: Growth vs Multiple
   - Bar: Key metrics vs peers

4. **Share price chart**
   - 2-year price history
   - Key events annotated

**Chart standards:**
- Dark theme or light theme (consistent with firm template)
- Labels in the appropriate language for the target market
- Source: `get_financial_statements`、`get_snapshots` 与 `get_valuations`；估值历史或自定义倍数用 `compute_factors`
- Include market-specific markers where relevant (e.g. price limits, earnings dates)

### Task 5: Report Assembly

**Standard initiation report format:**

```
【XX证券】[公司名称]（[代码]）首次覆盖报告：[评级]

投资要点：
  - [3-5 bullet investment highlights]
  - 目标价：[currency]XX.XX (X% upside/downside)
  - 评级：买入/增持/中性/减持/卖出

一、投资逻辑
   [Core thesis, 2-3 paragraphs]

二、公司分析
   - 业务概述
   - 竞争优势
   - 管理团队
   - 股权结构

三、行业分析
   - 市场规模与增速
   - 竞争格局
   - 政策环境
   - 发展趋势

四、财务分析
   - 历史财务表现
   - 盈利驱动因素
   - 财务健康度
   - 关键假设

五、估值分析
   - 估值方法概述
   - DCF analysis
   - Comparable companies
   - Valuation summary
   - Target price derivation

六、风险提示
   - [Company-specific risks]
   - [Industry risks]
   - [Market risks]
   - [Policy/regulatory risks]

附录：
   - 财务报表预测
   - 详细估值模型
   - 公司资料
```

**Report length:**
- Summary: 1 page
- Full report: 15-30 pages
- Financial model appendix: 10-15 pages

## Market-Specific Considerations

### Rating Conventions

| Rating | Chinese Equivalent | Implied Return |
|--------|-------------------|----------------|
| Buy | 买入 | >15% upside |
| Overweight / Accumulate | 增持 | 5-15% upside |
| Neutral / Hold | 中性 | -5% to +5% |
| Underweight / Reduce | 减持 | -5% to -15% |
| Sell | 卖出 | <-15% downside |

**Note:** Rating scales vary by report house and market — 高盛/摩根士丹利/中金 等主流卖方 use different conventions (some 5-point 买入/增持/中性/减持/卖出, some 3-point). Match the convention of the target market and house style.

### Coverage Initiation Practices

**Pre-initiation checklist:**
- [ ] Company filings reviewed (annual report, prospectus) — via `search_documents`(valuz-search，`market:ticker`)
- [ ] Management meeting completed (if possible)
- [ ] Industry research thorough
- [ ] Peer comparison complete
- [ ] Financial model built and validated
- [ ] Valuation analysis complete
- [ ] Conflicts of interest disclosed

**Common initiation triggers:**
- New IPO
- IPO quiet period expiry (typically 30-180 days)
- Market cap reaches coverage threshold
- New sector coverage mandate
- Material corporate action (M&A, restructuring)

### Market-Specific Risks to Highlight

| Risk Category | Examples |
|--------------|---------|
| 政策风险 | Regulatory changes, industry policy shifts |
| 市场风险 | Equity market volatility, sentiment swings |
| 流动性风险 | Low float, low turnover |
| 公司治理 | Related party transactions, controlling-shareholder risk |
| 行业风险 | Overcapacity, demand cyclicality |
| 汇率风险 | For exporters/importers |
| 商誉减值 | Goodwill impairment risk |
| 质押风险 | Share pledge unwinding (where applicable) |

### Regulatory Compliance

**Research compliance requirements (per the target market's regulator):**
- Investor suitability (投资者适当性管理)
- Conflict of interest disclosure (利益冲突披露)
- Research record retention (研究报告留痕)
- Quiet period rules before IPOs (静默期规定)
- Disclaimer on estimates (预测免责声明)

## Quality Checks

Before delivering:
- [ ] All financial data sourced from valuz-data / valuz-search
- [ ] Financial model balanced and validated
- [ ] Valuation analysis comprehensive
- [ ] Peer comparison adequate (>3 peers)
- [ ] Rating and target price justified
- [ ] Risk factors comprehensive
- [ ] Report follows standard format
- [ ] All citations complete
- [ ] Compliance disclosures included
