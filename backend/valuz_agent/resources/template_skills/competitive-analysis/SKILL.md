---
name: competitive-analysis
description: Competitive landscape analysis for global equities (focus US / HK / A-shares, also other markets). Maps competitors, compares positioning, and assesses relative strengths across markets. Uses `valuz-data` (get_industries, get_company, get_financial_statements, get_financial_statements, get_ownership) for peer sets, financials, and share data, and `valuz-search` (search_documents, search_documents, search_documents) for research, earnings calls, and filings. Triggers on "竞争格局", "行业竞争分析", "competitive landscape", "competitive analysis", or "[company] competitors".
---

# competitive-analysis

## Purpose

Analyze **全球股票市场（美股/港股/A 股为主，兼顾其他市场）行业竞争格局**, mapping competitive dynamics for companies and industries across markets.

## Data Sources

Two Valuz connectors cover everything this skill needs:

- `valuz-data` — 行情、财务、份额/指标数值数据 (quotes, financials, market-share and indicator figures).
- `valuz-search` — 财报、公告、研报、纪要、电话会检索 (earnings reports, filings, research, minutes, earnings calls).

Rule of thumb: 用 `valuz-data` 取财务/份额数据，用 `valuz-search` 取定性资料。

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.
`valuz-search` 用 `market:ticker` (`US:AAPL` / `HK:00700` / `SH:600519`)。

```text
get_industries(kind="list", ...) then get_industries(kind="constituents", industry_id=...)
get_company(kind="profile", symbol=...)                    → 业务描述
get_valuations(kind="latest", symbol=...)                  → 估值、市值
get_financial_statements(statement_type="revenue_breakdown", symbol=..., period="annual") → 营收结构
get_financial_statements(statement_type="income", symbol=..., period="annual") → 营收、毛利、净利
get_financial_statements(statement_type="balance", symbol=..., period="annual") → 资产、负债
get_ownership(symbol=...)                 (valuz-data) → 股东/控制权
search_documents(category="all", query=..., symbols=[...])         (valuz-search) → 卖方研报、竞争定性
search_documents(category="all", query=..., symbols=[...])     (valuz-search) → 电话会、管理层口径
search_documents(category="all", query=..., symbols=[...])         (valuz-search) → 公告、招股书、分部披露
```

Tickers span markets — US (`AAPL` / `US:AAPL`), HK (`00700` / `HK:00700`),
A-share (`600519` / `SH:600519`), and others.

### Secondary Sources
- Annual / segment reports — detailed segment data (`search_documents` via `valuz-search`)
- Sell-side industry reports — analyst competitive analysis (`search_documents` via `valuz-search`)
- Earnings-call commentary — management framing of rivals (`search_documents` via `valuz-search`)
- Market-share / revenue-mix data — `get_financial_statements` via `valuz-data`
- Industry associations — industry statistics

## Workflow

### Step 1: Map the Competitive Set

**Industry definition:**
```text
# Get full industry composition (cross-market peers) — valuz-data MARKET:LOCAL 规范代码输出
get_industries(kind="list", classification_system="GICS", level="industry")
# Select the matching spirits / liquor industry_id, then request constituents.
```

提示：`valuz-data` 用MARKET:LOCAL 规范代码 (`US:AAPL` / `HK:00700` / `SH:600519`)，`valuz-search` 用 `market:ticker` (`US:AAPL` / `HK:00700` / `SH:600519`)。

**Tier the competitors:**

| Tier | Description | Examples |
|------|-------------|---------|
| Tier 1 (龙头) | Market leaders, >10% share | {{SECTOR_LEADER}} ({{EXAMPLE_SECTOR}}) |
| Tier 2 (挑战者) | Strong #2-5, growing share | {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| Tier 3 (跟随者) | Niche players, regional | {{NICH_PLAYER}} |
| Tier 4 (边缘) | Declining or niche | {{LOW_END_PLAYER}} |

Peer/competitor sets are cross-market — a leader in one market may compete with challengers listed elsewhere (US / HK / A-share / others).

### Step 2: Competitive Comparison Matrix

每家竞争者用 `get_company` (valuz-data) 取业务描述/估值/市值，
`get_financial_statements` (valuz-data) 取营收、毛利、净利，`get_financial_statements`
(valuz-data) 比业务结构与份额；控制权/股东差异用 `get_ownership`
(valuz-data)。全部传MARKET:LOCAL 规范代码 (`US:AAPL` / `HK:00700` / `SH:600519`)。

**Core comparison table:**

| Company | Revenue | YoY | Gross Margin | Net Margin | ROE | Market Cap | PE (TTM) | Market Share |
|---------|---------|-----|-------------|------------|-----|-----------|----------|-------------|
| | | | | | | | | |

**Expand with competitive dimensions:**

| Dimension | Leader | Challenger 1 | Challenger 2 | Follower |
|-----------|--------|-------------|-------------|---------|
| 品牌力 (Brand) | | | | |
| 渠道能力 (Distribution) | | | | |
| 产品力 (Product quality) | | | | |
| 成本优势 (Cost advantage) | | | | |
| 研发投入 (R&D) | | | | |
| 国际化 (International) | | | | |

### Step 3: Market Share Analysis

份额/营收结构逐年取自 `get_financial_statements`(symbol, period="annual") (valuz-data)；
官方口径的市场地位/份额引述用 `search_documents` 或 `search_documents`
(valuz-search，`market:ticker` 代码，如 `US:AAPL`)。

**Share trends:**

| Company | 2020 | 2021 | 2022 | 2023 | 2024E | Trend |
|---------|------|------|------|------|-------|-------|
| | | | | | | ↑ / → / ↓ |

**Concentration metrics:**
- CR3, CR5, CR10 (top 3/5/10 concentration)
- HHI (Herfindahl-Hirschman Index)
- Market share distribution

### Step 4: Competitive Positioning

战略定位/护城河/管理层对竞争的定性判断，用 `search_documents`、
`search_documents` (valuz-search，`market:ticker` 代码) 检索研报观点、电话会口径与公告披露。

**Positioning map:**

For 2x2 matrices, use:
- X-axis: Price (价格) or Scale (规模)
- Y-axis: Quality (品质) or Growth (增速)

Example for {{EXAMPLE_SECTOR}}:
```
         高端/品质
           |
   {{SECTOR_LEADER}}    |   {{CHALLENGER_1}}/{{CHALLENGER_2}}
           |
   {{NICH_PLAYER}} |   {{NATIONAL_BRAND}}
           |
           |________________
              低端/性价比    高端/溢价
```

### Step 5: Barriers to Entry

**Common barriers:**

| Barrier Type | Examples |
|-------------|---------|
| 品牌护城河 | Consumer brand loyalty, 品牌认知 |
| 渠道壁垒 | Distribution network, 经销商体系 |
| 规模效应 | Cost advantages from scale |
| 技术壁垒 | Patents, know-how, 技术积累 |
| 牌照/资质 | Regulatory licenses, 牌照 |
| 资金壁垒 | Capital requirements |
| 政策壁垒 | Industry access / regulatory restrictions |
| 数据壁垒 | Data network effects |

### Step 6: Threat Assessment

**New entrants:**
- Likely sources (related industries, overseas)
- Barriers effectiveness

**Substitutes:**
- Alternative products/services
- Switching costs

**Supplier power:**
- Input concentration
- Price volatility (e.g., 原材料)

**Buyer power:**
- Customer concentration
- Switching costs

**Rivalry intensity:**
- Number and size of competitors
- Industry growth rate
- Differentiation level
- Exit barriers

### Step 7: Competitive Dynamics

价格战、产能扩张、并购、新品等竞争动态的定性证据，用 `search_documents`
(管理层口径)、`search_documents` (卖方观点)、`search_documents` (公告/交易披露)
(valuz-search，`market:ticker` 代码)；扩张/并购对营收结构的影响用 `get_financial_statements`
(valuz-data) 印证。

**Historical evolution:**
- How has competitive landscape changed?
- What drove shifts (policy, technology, demand)?

**Current dynamics:**
- Price competition (价格战)
- Capacity expansion
- M&A activity
- New product launches

**Future outlook:**
- Likely consolidation?
- New entrants?
- Technology disruption?

## Market-Specific Considerations

### Industry Structure

| Pattern | Description | Example Industries |
|---------|-------------|-------------------|
| 寡头垄断 | Few large players | spirits (top 5 >80%) |
| 分散竞争 | Fragmented, many players | restaurants, retail |
| 区域割据 | Regional champions | beer, food processing |
| 龙头集中 | Consolidating toward leaders | appliances, drug distribution |

### Competitive Behavior

- **价格战** (price wars) — common in commoditized sectors
- **渠道争夺** (channel competition) — 经销商, 线上平台 / online platforms
- **产能扩张** (capacity race) — leads to overcapacity
- **并购整合** (consolidation M&A) — industry rationalization
- **国际化** (going global) — emerging competitive frontier

### Regulatory & Government Role

- Industrial policy shapes competitive dynamics
- State-owned vs private competitive dynamics (e.g., A-share 国企 vs 民企)
- Local protectionism (地方保护) in some markets
- Antitrust / 反垄断 enforcement affects market structure (e.g., US DOJ/FTC, EU, China SAMR)

## Output Format

**Standard competitive analysis deliverable:**

```
【行业】竞争格局分析

一、行业概述
   市场规模, 增速, 发展阶段

二、竞争地图
   Tier划分, 市场份额

三、核心竞争要素
   各玩家优势对比

四、竞争动态
   价格, 渠道, 产能, 并购

五、壁垒分析
   进入壁垒, 现有壁垒有效性

六、趋势展望
   行业整合, 新进入者, 技术变革

七、结论与启示
   投资/战略含义
```

## Quality Checks

Before delivering:
- [ ] Competitive set complete and relevant (`get_industries`)
- [ ] Market share / revenue mix sourced (`get_financial_statements` + `search_documents`)
- [ ] Comparison matrix comprehensive
- [ ] Barriers analyzed
- [ ] Competitive dynamics explained
- [ ] Forward outlook included
- [ ] Strategic implications drawn
