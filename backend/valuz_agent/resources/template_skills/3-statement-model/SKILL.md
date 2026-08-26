---
name: 3-statement-model
description: Three-statement financial model for global equities (focus US / HK / A-shares, also other markets). Builds integrated IS/BS/CF models using valuz-data (get_financial_statements(statement_type=income|balance|cash_flow) / get_company) for three-statement history and revenue drivers, and valuz-search (search_documents / get_document(kind="raw_content")) for earnings reports, calls, research, minutes, and filings. Applies the accounting standard of each issuer (US GAAP / IFRS / CAS) and notes where line items differ by standard.
---

# 3-statement-model

## Purpose

Build institutional-quality three-statement models (Income Statement / Balance Sheet / Cash Flow) for 全球股票市场（美股/港股/A 股为主，兼顾其他市场）, applying the accounting standard that applies to each issuer (US GAAP / IFRS / CAS).

## Standard-Aware Parameters

Line items and conventions differ by the standard the issuer reports under. Determine the applicable standard first, then adapt.

| Parameter | Notes (varies by applicable standard) |
|-----------|---------------------------------------|
| Accounting standard | US GAAP / IFRS / CAS (企业会计准则) — pick per issuer |
| Tax rate | Use the applicable corporate rate for the issuer's jurisdiction (do not hard-code) |
| Currency | Reporting currency of the issuer (USD / HKD / CNY / …) |
| Fiscal year | Varies by issuer — confirm year-end |
| Revenue recognition | ASC 606 / IFRS 15 / CAS 14 — similar principles, confirm per standard |
| Goodwill | US GAAP / IFRS: indefinite life, impairment-tested; under some standards amortized — confirm per standard |
| R&D capitalization | Typically expensed under US GAAP; capitalizable under IFRS / CAS — confirm per standard |
| Indirect taxes (e.g. VAT) | Reported per local standard; often excluded from headline revenue — confirm per standard |
| Unit reporting | Use the local common unit for the issuer's reports — verify before modeling |

## Data Sources

Use `valuz-data` to pull three-statement history and line-item data; use `valuz-search` to retrieve original earnings reports and filings.

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.

### `valuz-data` — 三表历史数据、财务科目、营收拆分、公司画像

```text
get_financial_statements(statement_type="income", symbol, period="annual", limit=5) → 利润表
get_financial_statements(statement_type="balance", symbol, period="annual", limit=5) → 资产负债表
get_financial_statements(statement_type="cash_flow", symbol, period="annual", limit=5) → 现金流量表
get_financial_statements(statement_type="revenue_breakdown", symbol) → 营收拆分
get_company(kind="profile", symbol) → 公司画像
```

- `period` accepts `annual` or `quarterly`; `limit` controls how many periods to return (use a multi-period `limit` for historical depth).
- Tickers span markets: US (`AAPL`), HK (`00700`), A-share (`600519`), and others — always **bare** for `valuz-data`.

### `valuz-search` — 财报、公告原文检索

Retrieve original earnings reports, earnings calls, research, minutes, and regulatory filings to source and verify line items, disclosures, and footnotes.

```text
search_documents(category="all", query, symbols=["US:AAPL"])  → 定位财报文档 / Find earnings filings
get_document(kind="raw_content", document_id=...)                      → 取财报原文 / Fetch full filing text (科目口径核对)
get_document_chunks(kind="list", document_id=...)                            → 取文档结构化内容 / Fetch document content
```

Use `search_documents` (valuz-search) to locate the relevant filing, then `get_document(kind="raw_content")` / `get_document_chunks` (valuz-data) to pull the original text for line-item / 科目口径 reconciliation. Symbols use `market:ticker`.

## Workflow

### Step 1: Data Retrieval

用 `get_financial_statements`(valuz-data, period=annual, limit=5)拉 5 年历史三表；营收驱动用 `get_financial_statements`(valuz-data)，公司画像用 `get_company`(valuz-data)；财报原文/科目口径核对用 `search_documents`(valuz-search)+`get_document(kind="raw_content")`(valuz-data)。Pull 3-5 years of historical financials.

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.

**Verify units carefully:**
- Reporting unit varies by issuer/market (e.g. 元 / 千元 / thousands / millions)
- Normalize to a consistent unit before modeling

```text
# Pull all three statements (valuz-data, canonical MARKET:LOCAL symbol)
get_financial_statements(statement_type="income", symbol, period="annual", limit=5)
get_financial_statements(statement_type="balance", symbol, period="annual", limit=5)
get_financial_statements(statement_type="cash_flow", symbol, period="annual", limit=5)
# Revenue drivers + company profile
get_financial_statements(statement_type="revenue_breakdown", symbol)
get_company(kind="profile", symbol)
# Reconcile line items against original filings (valuz-search, market:ticker)
search_documents(category="all", query, symbols=["US:AAPL"])  →  get_document(kind="raw_content", document_id=...)
```

### Step 2: Historical Analysis

Document key trends:

- **Revenue growth**: YoY %, CAGR over 3-5 years (拆分驱动用 `get_financial_statements`)
- **Gross margin**: 毛利率 — track expansion/contraction
- **Operating margin**: 营业利润率
- **Net margin**: 净利率
- **Tax rate**: 所得税率 — flag if below the applicable statutory rate (possible incentives/preferential rate)
- **Effective tax rate**: vs statutory rate
- **D&A**: 折旧与摊销 as % of PPE
- **CapEx**: 资本支出 from cash flow statement
- **Working capital**: 应收账款、存货、应付账款 trends
- **Debt structure**: 有息负债 composition

### Step 3: Build the Model

Follow standard 3-statement modeling methodology, adapted to the applicable standard (US GAAP / IFRS / CAS):

#### Income Statement (利润表)

Key line items (naming may vary by standard; CAS naming shown as reference):
- 营业收入 (Revenue)
- 营业成本 (COGS)
- 税金及附加 (Taxes and surcharges — includes 消费税、城建税等; applies under CAS)
- 销售费用 (Selling expenses)
- 管理费用 (G&A)
- 研发费用 (R&D expenses — separated where the standard requires)
- 财务费用 (Financial costs — net of interest income)
- 营业利润 (Operating profit)
- 营业外收入/支出 (Non-operating income/expenses)
- 利润总额 (Profit before tax)
- 所得税费用 (Income tax expense)
- 净利润 (Net income)
- 归属于母公司股东的净利润 (Net income attributable to parent)

#### Balance Sheet (资产负债表)

Key line items:
- 货币资金 (Cash & equivalents)
- 交易性金融资产 (Trading financial assets)
- 应收账款 (Accounts receivable)
- 预付款项 (Prepayments)
- 存货 (Inventory)
- 其他流动资产 (Other current assets)
- 流动资产合计 (Total current assets)
- 固定资产 (Fixed assets / PPE)
- 在建工程 (Construction in progress)
- 无形资产 (Intangible assets)
- 商誉 (Goodwill — flag if >30% of equity)
- 非流动资产合计 (Total non-current assets)
- 资产总计 (Total assets)
- 短期借款 (Short-term borrowings)
- 应付账款 (Accounts payable)
- 预收款项/合同负债 (Advance receipts / contract liabilities)
- 一年内到期非流动负债 (Current portion of LT debt)
- 流动负债合计 (Total current liabilities)
- 长期借款 (Long-term borrowings)
- 应付债券 (Bonds payable)
- 非流动负债合计 (Total non-current liabilities)
- 负债合计 (Total liabilities)
- 股本 (Share capital)
- 资本公积 (Capital reserve)
- 盈余公积 (Surplus reserve)
- 未分配利润 (Retained earnings)
- 归属于母公司股东权益合计 (Parent equity)
- 少数股东权益 (Minority interest)
- 所有者权益合计 (Total equity)
- 负债和股东权益总计 (Total liabilities + equity)

#### Cash Flow Statement (现金流量表)

Indirect method:
- 经营活动现金流量 (Operating CF):
  - Start from 净利润
  - Adjust: D&A, 财务费用, 投资收益, 营运资本 changes
  - 经营活动产生的现金流量净额
- 投资活动现金流量 (Investing CF):
  - 购建固定资产/无形资产 (CapEx)
  - 处置收回 (Asset sales)
  - 投资支付/收回 (Investment purchases/sales)
- 筹资活动现金流量 (Financing CF):
  - 吸收投资 (Equity issuance)
  - 取得借款 (Borrowings)
  - 偿还债务 (Debt repayment)
  - 分配股利 (Dividends)
- 现金净增加额 (Net change in cash)
- 期末现金余额 (Ending cash balance)

### Step 4: Balance Checks

**CRITICAL checks:**

1. **Cash reconciliation**: 货币资金 (BS) = 期末现金余额 (CF) ± restricted cash
2. **BS balancing**: 资产总计 = 负债和股东权益总计
3. **RE roll-forward**: 期初未分配利润 + 本期净利润 - 提取盈余公积 - 现金分红 = 期末未分配利润
4. **CF-IS linkage**: 净利润 (IS) → starting point for 经营CF
5. **CapEx check**: 购建固定资产 (CF) ≈ 固定资产增加 (BS) + 累计折旧增加 (BS)
6. **Debt check**: 借款变动 (CF) = 短期借款变动 + 长期借款变动 (BS)
7. **Tax check**: 所得税费用 (IS) vs 实际缴纳 (CF indirect method add-back)

### Step 5: Scenario & Sensitivity

- Base / Bear / Bull scenarios
- Sensitivity on revenue growth, gross margin, tax rate
- Key drivers: 毛利率, 期间费用率, 营运资本效率

## Modeling Conventions (Standard-Aware)

### Indirect taxes (e.g. 增值税 / VAT)
- Headline 营业收入 is typically reported 按当地准则口径 (net of indirect taxes such as VAT where applicable)
- Under CAS, 税金及附加 includes 消费税 but not VAT
- Indirect taxes are usually off-balance-sheet in most models

### 商誉 (Goodwill)
- Common from M&A
- Flag if 商誉 > 30% of 归母权益
- Note impairment-testing (and, under standards that amortize, amortization) requirements

### 研发费用 (R&D)
- Treatment differs by standard: typically expensed under US GAAP; capitalizable under IFRS / CAS
- Check disclosures for capitalized R&D — flag if >5% of revenue

### 折旧 (Depreciation)
- Often straight-line over the asset's useful life
- Typical ranges:
  - Buildings: 20-40 years
  - Equipment: 5-10 years
  - Vehicles: 4-6 years

### 存货 (Inventory)
- Costing method (FIFO / weighted average / LIFO) depends on the applicable standard — LIFO is permitted under US GAAP but not under IFRS / CAS
- 存货计价 affects margin comparability across years

### 应收款项 (Receivables)
- 应收账款 often high for industrial companies — check DSO
- 应收票据 (notes receivable) vs 应收账款 (trade AR) — treat separately
- 坏账准备 (allowance for doubtful accounts) varies by company

### 借款 (Debt)
- 短期借款 often high for leveraged companies
- 长期借款 may include 一年内到期非流动负债
- 应付债券 for corporate bond issuers
- Interest expense in 财务费用 (net of interest income)

## Excel Formatting (OpenPyXL / Office JS)

Follow the same professional conventions as the base `xlsx-author` skill:

- **Section headers**: Dark blue `#1F4E79` with white bold text
- **Column headers**: Light blue `#D9E1F2` with black bold text
- **Input cells**: Blue font (RGB: 0,0,255) — all hardcoded inputs
- **Formula cells**: Black font (RGB: 0,0,0)
- **Sheet links**: Green font (RGB: 0,128,0)
- **Currency**: issuer reporting currency with thousands separator
- **Percentages**: 0.0% format
- **Cell comments**: "Source: valuz-data `get_financial_statements` (or valuz-data `get_document(kind="raw_content")`), [date], [field], [ticker]"

### Number Format Conventions

| Item | Format | Example |
|------|--------|---------|
| Revenue | #,##0 (with currency symbol) | 12,345 |
| Percentages | 0.0% | 15.3% |
| Ratios | 0.00x | 2.50x |
| Dates | YYYY | 2024 |
| Stock price | #,##0.00 (with currency symbol) | 158.50 |

## Quality Checks

Before delivering:

- [ ] All three statements balance correctly
- [ ] Cash from CF = Cash on BS
- [ ] Retained earnings roll-forward ties
- [ ] CapEx/Depreciation logic consistent
- [ ] Debt changes tie between BS and CF
- [ ] Tax rate reasonable for the issuer's jurisdiction (flag if below the applicable statutory rate)
- [ ] All hardcoded inputs have cell comments
- [ ] All formulas reference cells, no hardcodes in formulas
- [ ] Scenario blocks structured correctly (Bear/Base/Bull)
- [ ] Sensitivity analysis included

## Common Pitfalls

1. **Unit mismatch**: Mixing reporting units (e.g. 元 / 千元 / thousands / millions) — always normalize
2. **Indirect-tax confusion**: Double-counting VAT (or other indirect taxes) in revenue or COGS
3. **Goodwill spike**: Forgetting large goodwill from acquisitions
4. **Tax rate variance**: Assuming a generic rate when a preferential/incentive rate applies
5. **R&D treatment**: Differs by standard — expensed vs capitalized; check disclosures
6. **Receivables inflation**: 应收账款 much higher than revenue growth — flag
7. **Short-term debt**: 短期借款 often high; don't miss current portion of LT debt
8. **Minority interest**: 少数股东权益 can be significant for conglomerates

## Data Source Priority

用 `get_financial_statements` / `get_company`(valuz-data, MARKET:LOCAL 规范代码)取三表历史与科目数据，用 `search_documents`+`get_document(kind="raw_content")`(valuz-search, `market:ticker`)取财报/公告原文。

1. **valuz-data** (`get_financial_statements` / `get_company`) — three-statement history, financial line items, revenue drivers, company profile
2. **valuz-search + valuz-data** (`search_documents` → `get_document(kind="raw_content")` / `get_document_chunks`) — original earnings reports, calls, research, minutes, filings
3. **Web search** — only if the above are insufficient; mark `[UNSOURCED]`

## Output Checklist

- [ ] Three-statement model fully linked (IS → BS → CF)
- [ ] All balance checks pass
- [ ] Historical + projected years included
- [ ] Scenario analysis (Bear/Base/Bull)
- [ ] Sensitivity tables
- [ ] All hardcoded inputs sourced and commented
- [ ] Applicable accounting standard (US GAAP / IFRS / CAS) documented
- [ ] Currency consistent (issuer reporting currency)
- [ ] File named: `[Ticker]_3StatementModel_[Date].xlsx`
