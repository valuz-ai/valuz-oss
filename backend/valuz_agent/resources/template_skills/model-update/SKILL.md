---
name: model-update
description: Update global equity financial models with new quarterly data, management guidance, or macro changes. Reflects actuals, rolls estimates forward, flags material changes, and updates valuation. Works across global stock markets (US / HK / A-shares primary, other markets supported), using valuz-data (quotes, financials, indicators) and valuz-search (filings, calls, research). Triggers on "模型更新", "plug earnings into model", "update model for [company]", "刷新预测", or "更新财务模型".
---

# model-update

## Purpose

Update existing **financial models for global equities**（全球股票市场：美股 / 港股 / A 股为主，兼顾其他市场）with new data, ensuring all cells are traceable to sources and all changes are documented.

## Data Sources

> **代码格式 / Symbol format**：valuz-data 用MARKET:LOCAL 规范代码（`US:AAPL` / `HK:00700` / `SH:600519`）；valuz-search 用 `market:ticker`（`US:AAPL` / `HK:00700` / `SH:600519`）。

### Primary: valuz-data (实际/历史财务、LTM、指标数值)

落最新实际三表、刷新 LTM、刷新价格/市值（MARKET:LOCAL 规范代码）：

```
get_financial_statements(statement_type="income", symbol, period="quarterly", limit=N) → actual income statement
get_financial_statements(statement_type="balance", symbol, period="quarterly", limit=N) → BS update
get_financial_statements(statement_type="cash_flow", symbol, period="quarterly", limit=N) → CF update
get_financial_statements(statement_type="income", symbol, period="annual", limit=N) → full-year update
get_financial_statements(statement_type="revenue_breakdown", symbol) → revenue mix
get_snapshots(symbol)                                      → current price and market activity
get_valuations(kind="latest", symbol)                     → market cap and valuation multiples
get_company(kind="profile", symbol)                       → company changes
```

> `period` 取 `quarterly` 或 `annual`；`limit` 拉取多期以便对齐历史列与 LTM 计算。

### Secondary: valuz-search (财报/公告原文)

落数后用财报原文逐项核对（同样使用规范 `MARKET:LOCAL` symbol）：

```
search_documents(category="all", query, symbols=["US:AAPL"])  → 定位业绩/财报文档 / locate earnings docs
search_documents(category="all", query, symbols=["SH:600519"]) → 官方公告、监管披露 / official filings
get_document(kind="raw_content", document_id=...)                    → 取原文核对精确数字 / exact figures from source
get_document(...)                        → 管理层评论、业绩说明会要点 / management commentary, call highlights
search_documents(category="all", query, symbols=[...])         → 卖方研究、一致预期更新 / sell-side & consensus updates
```

- **search_documents + get_document(kind="raw_content")** — official filings for exact figures (financial reports, regulatory disclosures)
- **业绩说明会 / earnings call transcript** — management commentary（`search_documents` / `get_document`）
- **管理层指引 / guidance** — guidance from earnings calls
- **Research & consensus** — sell-side estimate updates（`search_documents`）

## Workflow

### Step 1: Identify What Changed

**Change triggers:**
- Quarterly earnings release (季报/年报 / quarterly / annual report)
- Management guidance update (管理层指引调整)
- Macro assumption change (rate, tax, policy)
- Model error or refinement
- M&A or restructuring event

**Change log template:**

| Date | Change Type | Item | Old Value | New Value | Reason |
|------|-------------|------|-----------|-----------|--------|
| | Earnings update | Revenue FY25E | XX | XX | Q1 actuals beat |
| | Guidance | Tax rate | 25% | 25% | No change |
| | Macro | CapEx % | 5% | 6% | New plant announced |

### Step 2: Update Historical Actuals

**Quarterly actuals:**

用 `get_financial_statements`(valuz-data, period="quarterly", limit=N) 落最新实际数并刷新 LTM；用 `search_documents`(valuz-search)+`get_document(kind="raw_content")`(valuz-data) 取财报原文逐项核对。

```
[Company] Q[X] 20XX Actuals (from get_financial_statements(statement_type=income|balance|cash_flow)，原文核对 search_documents+get_document(kind="raw_content")):
- Revenue 营业收入: XXX (YoY: +XX%)   # 按当地准则口径
- Gross margin 毛利率: XX% (vs prior: XX%)
- Net income 归母净利润: XXX (YoY: +XX%)
- EPS: X.XX
- Operating cash flow 经营现金流: XXX
```

> Amounts are reported in the issuer's local convention（按当地常用单位）and revenue is stated under local accounting conventions（按当地准则口径）.

**Update sequence:**
1. Drop Q[X] actuals into historical columns
2. Verify sum checks (quarterly sum = annual)
3. Update LTM (Last Twelve Months) calculations
4. Check annual-to-quarter relationships

### Step 3: Roll Forward Estimates

**Revenue projections:**
- Update growth rates based on Q[X] performance
- Consider:
  - Order backlog changes
  - New product ramp
  - Market share gains/losses
  - Capacity expansion

**Margin projections:**
- Update gross margin based on actual trend
- Update opex ratios based on actual leverage
- Flag structural changes (input costs, pricing power)

**Balance sheet projections:**
- Update working capital assumptions
- Update debt schedule if refinancing occurred
- Update CapEx plans

### Step 4: Update Valuation

**Market data refresh:**

```
get_snapshots(symbol)  # 当前价格 / current price
get_valuations(kind="latest", symbol)  # 市值与估值倍数 / market cap, PE, PB
```

**Valuation metrics to update:**
- Current stock price and change
- Trading multiple (PE, PB, EV/Sales, EV/EBITDA)
- 52-week range position
- Relative to sector median
- Implied growth vs historical

**DCF updates (if applicable):**
- Roll forward projections by one year
- Update market data inputs (beta, risk-free rate, shares)
- Update terminal growth if outlook changed

### Step 5: Flag Material Changes

**Change significance assessment:**

| Change | Threshold | Action |
|--------|-----------|--------|
| Revenue estimate | ±5% | Update model, note driver |
| Net income estimate | ±10% | Update model, revise target price |
| Margin estimate | ±100 bps | Update model, assess sustainability |
| CapEx estimate | ±20% | Update model, check FCF impact |
| Multiple assumption | ±1x | Sensitivity check, document rationale |

**Red flags to highlight:**
- Revenue growth deceleration >500 bps
- Margin compression >300 bps
- Working capital deterioration
- Debt increase >20% vs prior forecast
- Management guidance lower than consensus

### Step 6: Document Changes

**Model update memo:**

```
[公司名称 / Company]（[代码 / Ticker]）模型更新 / Model Update [Date]

一、更新内容 / What changed
   [Bullet list of changes made]

二、关键变动 / Key revisions
   Revenue FY25E: [Old] → [New] ([Change]%)
   Net Income FY25E: [Old] → [New] ([Change]%)
   Driver: [Explanation]

三、估值影响 / Valuation impact
   新目标价 / New target price: XX.XX (之前 / prior XX.XX)   # local currency
   调整幅度 / Adjustment: +X% / -X%
   调整逻辑 / Rationale: [Brief rationale]

四、后续关注 / Watch list
   - [Next catalyst]
   - [Key metric to monitor]
   - [Risk factor]
```

### Step 7: QC Checklist

**Before finalizing:**

- [ ] All Q[X] actuals sourced from `get_financial_statements`(valuz-data) and cross-checked against `search_documents`(valuz-search)+`get_document(kind="raw_content")`(valuz-data)
- [ ] Historical data matches reported figures exactly
- [ ] All formulas intact (no broken references)
- [ ] LTM calculations updated
- [ ] Forward estimates reflect new information
- [ ] Valuation inputs refreshed (price, shares, multiples)
- [ ] Target price recalculated
- [ ] Change log complete
- [ ] Cell comments added for new inputs
- [ ] Model balances correctly

## Market-Aware Update Considerations

### Earnings Season Timing

Reporting cadence varies by market — confirm the issuer's filing calendar. A representative pattern:

| Report | Deadline (market-dependent) | Typical Release Window |
|--------|-----------------------------|------------------------|
| Q1 季报 | e.g. Apr 30 | Apr 1-30 |
| 中报 / Interim | e.g. Aug 31 | Aug 1-31 |
| Q3 季报 | e.g. Oct 31 | Oct 1-31 |
| 年报 / Annual | e.g. Apr 30 | Jan-Apr |

> US issuers report quarterly (10-Q / 10-K), HK issuers typically half-yearly plus quarterly updates, A-share issuers on the calendar above — adapt to the actual market.

**Model update priority:**
- Annual report: Complete overhaul of historicals
- Semi-annual / interim: Major update, adjust full-year estimates
- Quarterly: Incremental update, verify full-year trajectory

### Pre-Announcement Integration

If company issued a pre-announcement (业绩预告 / preliminary results / guidance):
- Use as directional signal before formal report
- Adjust estimates if variance >20% from prior
- Flag for detailed update when formal report arrives

### Consensus Management

- Update consensus assumptions based on new information
- If company guidance differs from consensus, flag divergence
- Note if management commentary suggests estimate revision

### Regulatory & Accounting Changes

Monitor for:
- Tax rate changes (e.g. preferential-status reclassification)
- Accounting standard updates — apply the issuer's basis (**US GAAP / IFRS / CAS**)
- Industry-specific regulation impacts on assumptions
- Dividend policy changes (影响 DCF terminal value / affects DCF terminal value)

## Quality Checks

Before delivering:
- [ ] All new actuals traceable to source
- [ ] Formulas verified (no hardcodes in calculations)
- [ ] Estimates logically consistent with new data
- [ ] Valuation update reflects current market conditions
- [ ] Changes documented in update memo
- [ ] Model passes basic QC (balance checks, sum checks)
