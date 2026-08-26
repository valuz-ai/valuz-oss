---
name: earnings-preview
description: Pre-earnings analysis for global equities (focus US / HK / A-shares, also other markets). Builds scenario frameworks (actual vs consensus, beat/miss cases), identifies key metrics to watch, and prepares positioning notes before companies report quarterly results. Uses valuz-data (get_calendar, get_financial_statements, get_financial_statements, get_company, get_snapshots) and valuz-search (search_documents 一致预期/前瞻, search_documents guidance, search_documents, search_documents, search_documents). Triggers on "财报前瞻", "季报前瞻", "业绩前瞻", "earnings preview", "what to watch for [company] earnings", or "pre-earnings setup".
---

# earnings-preview

## Purpose

Build **全球上市公司（美股/港股/A 股为主，兼顾其他市场）季报/年报前瞻分析**, preparing for company earnings releases with scenario frameworks and key metrics to watch.

## Data Sources

用 `valuz-data` 定档期 + 取历史财务基线，用 `valuz-search` 取一致预期/指引/电话会/公告。

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.

- **`valuz-data`** — 财报日程、历史财务、指标数值数据。
- **`valuz-search`** — 一致预期/前瞻、指引、电话会、公告、纪要检索。

```python
# valuz-data — MARKET:LOCAL 规范代码 (US:AAPL / HK:00700 / SH:600519)
get_calendar(symbol="US:AAPL")                      → 下次财报档期、报告期
get_financial_statements(statement_type="income", symbol="US:AAPL", period="quarterly", limit=8) → 历史营收/利润/EPS 趋势
get_financial_statements(statement_type="revenue_breakdown", symbol="US:AAPL", period="quarterly") → 分部/分产品收入结构
get_company(kind="profile", symbol="US:AAPL")      → 公司画像
get_valuations(kind="latest", symbol="US:AAPL")   → 估值、PE/PB、市值
get_snapshots(symbol="US:AAPL")                            → 当前价、交易区间
# valuz-search — market:ticker (US:AAPL / HK:00700 / SH:600519)
search_documents(category="all", query="一致预期 前瞻", symbols=["US:AAPL"])  → 卖方一致预期、前瞻研报
search_documents(category="all", symbols=["US:AAPL"])               → 上次电话会指引
search_documents(category="all", query="业绩预告 指引", symbols=["US:AAPL"]) → 近期催化、指引新闻
search_documents(category="all", symbols=["US:AAPL"])                   → 业绩预告、公告
search_documents(category="all", symbols=["US:AAPL"])                  → 历史财报披露文档
```

### Consensus Estimates

- 一致预期/前瞻用 `search_documents`(valuz-search，`query="一致预期"` 或 `"前瞻"`)。
- **If consensus unavailable**, derive from:
  - 历史增速基线：`get_financial_statements`(valuz-data)
  - 上次指引：`search_documents`(valuz-search) 取电话会指引
  - Industry benchmarks

### Secondary Sources (via `valuz-search`)
- 公司公告/业绩预告 (earnings preview notices) — `search_documents`
- 历史财报披露文档 — `search_documents`
- 卖方研报/前瞻 (broker research) — `search_documents`
- 电话会指引/纪要 (earnings call) — `search_documents`
- 近期催化/指引新闻 — `search_documents`

## Workflow

### Step 1: Establish Baseline

先用 `get_calendar`(valuz-data，MARKET:LOCAL 规范代码) 确认下次财报档期与报告期；历史基线用 `get_financial_statements`(valuz-data，`period="quarterly"`, `limit=8`)，收入结构用 `get_financial_statements`(valuz-data)。

**Historical performance (last 4-8 quarters):**

| Quarter | Revenue | YoY | Net Income | YoY | EPS | Net Margin |
|---------|---------|-----|------------|-----|-----|------------|
| Q1 2024 | | | | | | |
| Q2 2024 | | | | | | |
| Q3 2024 | | | | | | |
| Q4 2023 | | | | | | |

**Identify trends:**
- Accelerating or decelerating growth?
- Margin expansion or compression?
- Seasonal patterns?
- One-time items to normalize?

### Step 2: Gather Consensus Estimates

一致预期与前瞻用 `search_documents`(valuz-search，`query="一致预期"`/`"前瞻"`, `symbols=["US:AAPL"]`)；上次管理层指引用 `search_documents`(valuz-search)；近期指引/预告新闻用 `search_documents`(valuz-search)。

**Consensus table:**

| Metric | Q1 2024 Estimate | Range (Low-High) | # Analysts |
|--------|-----------------|-------------------|------------|
| Revenue | | | |
| YoY Growth | | | |
| Net Income | | | |
| EPS | | | |
| Gross Margin | | | |
| Net Margin | | | |

**Beat probability assessment:**
- Strong beat (>+10%): Company has history of under-promising
- Moderate beat (+5% to +10%): Consensus well-established
- In-line (-5% to +5%): Typical range
- Miss risk (<-5%): Macro headwinds, order delays

### Step 3: Identify Key Metrics to Watch

**Company-specific KPIs:**

For each company, identify 3-5 metrics that will drive the report:

| Metric | Why It Matters | Watch Threshold | Risk if Missed |
|--------|---------------|-----------------|----------------|
| e.g., 白酒批价 | Price indicator for channel health | >950元/瓶 | Demand softness |
| e.g., iPhone units / ASP | Volume & mix indicator | > prior-year | Demand softness |
| e.g., 云业务收入增速 | Growth engine health | >30% | Cloud slowdown |

**Sector-wide KPIs (for sector previews):**

| Sector | Key Metrics |
|--------|-------------|
| 白酒 / Spirits | 批价、库存、回款、动销 |
| 半导体 / Semiconductors | 产能利用率、出货量、ASP、库存天数 |
| 新能源汽车 / EVs | 交付量、单车收入、毛利率、电池成本 |
| 医药 / Pharma | 创新药收入、研发费用、集采/定价影响 |
| 银行 / Banks | NIM、不良率、拨备覆盖率 |
| 券商 / Brokers | 经纪/投行/资管收入、股基交易量 |
| 光伏 / Solar | 硅料/组件价格、排产、海外出货 |
| 房地产 / Real estate | 销售额、拿地、融资成本 |

### Step 4: Build Scenario Framework

**Three-scenario model:**

```
BEAR CASE (超预期悲观)
  Revenue: -X% vs consensus
  Net Income: -Y% vs consensus
  Key factor: [specific risk]
  Likely catalysts: 业绩预告大幅下调, 行业负面政策 (核对 `search_documents`)

BASE CASE (符合预期)
  Revenue: ±Z% vs consensus
  Net Income: ±W% vs consensus
  Key factor: [steady state]
  Likely outcome: 符合预期, 股价波动±5%

BULL CASE (超预期乐观)
  Revenue: +A% vs consensus
  Net Income: +B% vs consensus
  Key factor: [positive surprise driver]
  Likely catalysts: 新品放量, 成本下降超预期
```

### Step 5: Position Analysis

**What does the market expect?**

当前估值/股价用 `get_company` 与 `get_snapshots`(valuz-data，MARKET:LOCAL 规范代码)；卖方评级分布用 `search_documents`(valuz-search)。

- Recent stock price performance into earnings
- Implied move from options (if listed options available)
- Sentiment from fund flows (e.g. 北向资金 for A-shares, ETF/institutional flows elsewhere)
- Broker recommendations distribution

**Position sizing considerations:**
- High expectations (high PE) → asymmetric risk to downside
- Low expectations (depressed stock) → upside potential on beat
- Earnings as catalyst: upcoming product launch, policy change

### Step 6: Pre-Earnings Positioning Note

**Standard structure:**

```
[公司名称]（[代码]）[季/年报] 前瞻：[主题/焦点]

一、业绩预期
  - 关键指标一致预期一览
  - 预测区间

二、情景分析
  - 乐观/基准/悲观情景

三、关注要点
  - 最重要的 3-5 个指标
  - 预期 vs 实际的关键差异点

四、估值与预期
  - 当前估值水平
  - 市场情绪指标
  - 资金动向（如北向资金 / 机构资金流向）

五、情景判断与策略
  - 不同情景下的股价反应
  - 可能的交易策略

六、风险提示
  - 关键下行风险
```

### Step 7: Post-Earnings Follow-up

After actual results are released:
- Compare actual vs preview scenarios
- Update the earnings-analysis model
- Revise forward estimates
- Note any material guidance changes

## Market-Aware Pre-Earnings Considerations

### Earnings Calendar

Earnings-season conventions vary by market — confirm each company's reporting
calendar and deadlines for its listing venue rather than assuming a single
schedule. 用 `get_calendar`(valuz-data，MARKET:LOCAL 规范代码) 取该公司下次披露日期与报告期。

| Report Type | Typical Cadence | Typical Release Time |
|-------------|-----------------|----------------------|
| Quarterly (e.g. Q1 / Q3季报, US 10-Q) | Within weeks–months of quarter-end, per venue | Before market open or after close |
| Semi-annual report (中报 / interim) | Within months of period-end, per venue | Before market open |
| Annual report (年报 / 10-K) | Within months of year-end, per venue | Varies by market |

**Release pattern:**
- Many companies release before market open or after market close
- Tickers span US (e.g. AAPL), HK (e.g. 0700.HK), and A-share (e.g. 600519.SH)
- Some venues (e.g. 创业板/科创板) may have more flexible schedules

### 业绩预告 / Pre-Announcements

- Some markets mandate earnings pre-announcements above a variance threshold
  (e.g. A-share 业绩预告 when actual vs prior period variance is large);
  others rely on voluntary guidance or pre-announcements.
- Published typically weeks before the formal report
- A-share format examples: 预增 (increase), 预减 (decrease), 扭亏 (turn to profit), 首亏 (first loss), 续亏 (continued loss)
- Provides directional guidance before formal report
- 检索：`search_documents`(valuz-search，`symbols=["SH:600519"]`) 或 `search_documents`(valuz-search，`query="业绩预告"`)

### Consensus Reliability

**Caveats for consensus across markets:**
- Coverage breadth varies — large-cap US names typically have many analysts;
  smaller or non-US names may have fewer
- Estimates may be stale (update frequency varies by market)
- Institutional vs retail analyst coverage varies significantly
- Broker research sometimes biased (conflicted interests)
- Cross-reference multiple sources when possible

### Policy & Regulatory Risk

- Regulatory changes can materially impact earnings overnight
- Industry policy shifts are common across markets, e.g.:
  - 医药 / Pharma (集采 / drug pricing)
  - 教育 / Education (regulatory tightening)
  - 互联网 / Internet (antitrust)
  - 新能源 / Renewables (subsidy / incentive changes)
- Factor policy risk into scenario analysis

## Quality Checks

Before delivering preview:

- [ ] Historical data complete and accurate (verified via `get_financial_statements`, valuz-data)
- [ ] Consensus estimates sourced via `search_documents` (valuz-search) — or clearly noted as unavailable
- [ ] Scenario framework covers bull/base/bear
- [ ] Key watch items identified with rationale
- [ ] Market-specific risks flagged (政策, 集采, regulatory, etc.)
- [ ] Valuation context included
- [ ] Pre-earnings positioning actionable
- [ ] Disclosure source confirmed on the right platform (SEC EDGAR、港交所披露易、巨潮资讯等)
