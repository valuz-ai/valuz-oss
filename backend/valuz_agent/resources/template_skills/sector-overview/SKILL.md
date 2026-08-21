---
name: sector-overview
description: Comprehensive global equity sector and industry landscape reports — market size, competitive positioning, policy environment, key players, trading multiples, and thematic trends. Uses valuz-data for structured data and valuz-search for categorized document discovery. Triggers on "行业分析", "行业研究", "板块分析", "sector overview", "sector deep dive", "[industry] 行业分析", or "[industry] landscape".
---

# sector-overview

## Purpose

Create comprehensive **行业/板块深度报告 (industry/sector deep-dive)** covering market dynamics, competitive positioning, regulatory environment, key players, trading multiples, and thematic trends across **全球股票市场（美股/港股/A 股为主，兼顾其他市场）**.

## Data Sources

Two Valuz connectors cover everything you need:

- **`valuz-data`** (Valuz Data MCP) — 行情、财务、行业/指数成分、概念主题；也负责读取搜索命中的文档与引用 chunk。
- **`valuz-search`** (Search MCP) — 财报、公告、研报、纪要、电话会、新闻发现。

**两个连接器都使用 `MARKET:LOCAL` 规范代码（`US:AAPL` / `HK:00700` / `SH:600519`）；非规范输入先调用 `resolve_symbols`。**

Rule of thumb: **用 `valuz-data` 取行情/财务/行业成分/概念热度，用 `valuz-search` 取定性资料（研报/政策/新闻）。**
注意：**没有专门的"宏观经济序列"MCP 工具** —— 行业规模、GDP/CPI/利率等宏观数据通过 `search_documents`（行业/宏观/策略研报）、`search_documents`(valuz-search) 检索，或作为分析师输入。

```python
# 板块/赛道玩家清单与基准 (valuz-data, MARKET:LOCAL 规范代码)
get_industries(kind="constituents", ...)          → 行业/赛道成分股
get_index(kind="constituents", ...)               → 指数成分股（大盘基准）
get_themes(kind="list")                           → 主题/概念列表
get_company(kind="profile", symbol="US:AAPL")    → 个股基本面对照
get_snapshots(symbol="US:AAPL")                   → 个股行情
get_valuations(kind="latest", symbol="US:AAPL")  → 市值与估值倍数
get_financial_statements(statement_type="income", symbol="US:AAPL", period="annual") → 财务数据
# 行业规模/政策/研报/新闻 (valuz-search, market:ticker)
search_documents(category="all", query="...", symbols=["US:AAPL"])  → 行业/宏观/策略研报
search_documents(category="all", query="...")                          → 政策、行业新闻
```

## Workflow

### Step 1: Define Scope & Angle

**Clarify:**
- Sector / industry focus (e.g., Semiconductors, EV / 新能源汽车, Spirits / 白酒)
- Angle (e.g., investment theme, competitive analysis, turnaround story)
- Geographic scope (target market — US, HK, A-share, or cross-listed / global peers)
- Time horizon (cyclical analysis, secular trends, near-term trade)

### Step 2: Market Size & Structure

**Industry overview:**

| Metric | Data | Source |
|--------|------|--------|
| Total market size | 市场规模 (target market) | `search_documents`(valuz-search, 行业/宏观研报) |
| Growth rate | 同比增速 / CAGR | `search_documents`(valuz-search)，或个股 `get_financial_statements`(valuz-data) 汇总 |
| Market share | CR5, CR10 concentration | `search_documents`(valuz-search) |
| Penetration rate | For new industries | `search_documents`(valuz-search, 行业研报) |
| Export/import | 进出口数据 | `search_documents`(valuz-search, 宏观/贸易) |

**Listed representation (in the target market):**
- Number of listed companies in sector
- Total market cap (总市值 / 流通市值)
- Average PE / PB multiples
- Liquidity (average daily turnover)

### Step 3: Competitive Landscape

**Peer mapping:**

```python
# 拉赛道玩家清单 (valuz-data, MARKET:LOCAL 规范代码)
get_industries(kind="constituents", ...) → 行业/赛道成分股
get_index(kind="constituents", ...)      → 指数成分（用指数代表板块时）
get_themes(kind="list")                  → 主题列表
```

**For each major player, pull (valuz-data, MARKET:LOCAL 规范代码):**
```python
get_snapshots(symbol="US:AAPL")                              → Price and market activity
get_valuations(kind="latest", symbol="US:AAPL")             → Market cap and multiples
get_financial_statements(statement_type="income", symbol="US:AAPL", period="annual") → Revenue and margins
get_company(kind="profile", symbol="US:AAPL")               → Business description
```

**Competitive analysis table:**

| Company | Ticker | Market Cap | Revenue | YoY Growth | Gross Margin | Net Margin | PE (TTM) | Market Share |
|---------|--------|-----------|---------|------------|-------------|------------|----------|-------------|
| | | | | | | | | |

**Competitive positioning:**
- Market share trends (gaining or losing)
- Product positioning (premium, mid-tier, mass market)
- Geographic footprint (national vs regional vs global)
- Distribution strength (渠道能力)
- Brand equity (品牌力)

### Step 4: Trading Multiples & Valuation

**Sector multiples:**

| Metric | Current | 1Y High | 1Y Low | 5Y Average | Historical High | Historical Low |
|--------|---------|---------|--------|------------|----------------|----------------|
| Average PE | | | | | | |
| Average PB | | | | | | |
| Average PS | | | | | | |
| EV/EBITDA | | | | | | |

**Multiple dispersion:**
- Premium names (high multiple vs sector)
- Value names (low multiple vs sector)
- Historical range and where we are in cycle

### Step 5: Policy Environment

**Cross-market policy analysis:**

| Policy Area | Current Status | Impact |
|-------------|---------------|--------|
| 产业政策 | 支持/限制/中性 | Direct sector impact |
| 环保 / 气候政策 | 减碳目标 | Cost structure impact |
| 监管政策 | 反垄断、行业监管 | Margin / market share impact |
| 贸易政策 | 关税与出口管制 | Competitiveness impact |
| 货币政策 | 宽松/中性/紧缩 | Financing cost impact |

**Key regulatory bodies (market-dependent):**
- Industrial / pricing regulators (e.g., NDRC, FTC, agency overseeing 产业政策与定价)
- Tech / telecom / manufacturing regulators (e.g., MIIT, FCC)
- Environmental regulators (e.g., MEE, EPA)
- Healthcare / pharma regulators (e.g., NHC, FDA)
- Capital-markets regulators (e.g., CSRC, SEC)
- Central banks (e.g., PBoC, Fed) — monetary policy

用 `search_documents`（行业/宏观/策略研报）与 `search_documents`(valuz-search) 拉取相关市场的最新政策、监管事件与新闻。

### Step 6: Key Drivers & Trends

**Secular trends (3-5 year):**
- Technology adoption (技术替代)
- Consumption upgrade/downgrade (消费升级/降级)
- Demographic shifts (人口结构)
- Supply chain localization / reshoring (供应链国产化/回流)
- Environmental transition (减碳/ESG)

**Cyclical factors:**
- Inventory cycles (库存周期)
- Capacity utilization (产能利用率)
- Pricing trends (价格趋势)
- Demand momentum (需求 momentum)

### Step 7: Investment Themes

**Thematic angles:**

1. **Policy-driven**: 国产替代/reshoring, 新基建/infrastructure, 产业补贴
2. **Demand-driven**: 消费升级, 老龄化, 出海/globalization
3. **Supply-driven**: 产能出清, 行业整合, 龙头集中
4. **Technology-driven**: AI应用, 新能源, 生物医药创新

**For each theme:**
- Investment thesis
- Key names to express the theme
- Timeline and catalysts
- Risks and headwinds

### Step 8: Trading Ideas

**Ideas shortlist (from sector overview):**

| Idea | Ticker | Direction | Thesis | Catalyst | Risk |
|------|--------|-----------|--------|----------|------|
| | | Long / Short | | | |

### Step 9: Report Structure

**Standard sector overview format:**

```
[Research] [行业 / Industry] 深度报告：[标题]

一、行业概述
   市场规模、增速、发展阶段

二、竞争格局
   主要玩家、市场份额、竞争要素

三、政策环境
   监管框架、产业政策、影响分析

四、财务分析
   行业盈利能力、ROE、杠杆水平

五、估值分析
   当前 multiples vs 历史 vs 国际对标

六、核心驱动因素
   长期趋势、短期催化、周期位置

七、投资建议
   重点标的、目标价、评级

八、风险提示
   政策风险、需求风险、竞争风险
```

## Market-Specific Considerations

### Market Structure (varies by listing venue)

| Feature | Description | Implication |
|---------|-------------|-------------|
| 涨跌停 / 限制 | e.g. A-share ±10% (main), ±20% (创业板/科创板); US no daily limit | Price discovery speed varies |
| 散户占比 | High in A-share (~60-80% of turnover); lower in US | Sentiment vs institution driven |
| 政策驱动 | Government announcements can move markets (esp. A-share/HK) | Monitor policy closely |
| 板块轮动 | Sector rotation common | Timing important |
| 跨境资金 | Foreign / 北向资金 flows tracked | Sentiment indicator |

Example tickers across markets — valuz-data MARKET:LOCAL 规范代码: **AAPL** (US), **00700** (HK), **600519** (A-share); valuz-search `market:ticker`: **US:AAPL**, **HK:00700**, **SH:600519**.

### Industry Classification

**Common sector classifications:**
- GICS — global standard, widely used for US/global names
- 申万行业分类 (SW Industry) — common for A-shares
- 中信行业分类 (CITIC) — alternative for A-shares
- Regulatory / data-vendor classifications

**用 `get_industries`(valuz-data) 拉取与目标市场匹配的行业成分股；以指数代表板块时改用 `get_index`(valuz-data)。**

### Sector-Specific Metrics

| Sector | Key Metrics |
|--------|-------------|
| Spirits / 白酒 | 批价, 库存, 回款, 动销, 渠道库存 |
| Semiconductors / 半导体 | 产能利用率, 出货量, ASP, 库存天数 |
| EV / 新能源汽车 | 交付量, 单车收入, 电池成本, 渗透率 |
| Pharma / 医药 | 创新药收入, 研发费用率, 招采中标 |
| Banks / 银行 | NIM, 不良率, 拨备覆盖率, ROE |
| Brokers / 券商 | 经纪/投行/资管收入, 股基交易量, 两融余额 |
| Solar / 光伏 | 硅料/组件价格, 排产, 海外出货 |
| Real estate / 房地产 | 销售额, 拿地, 融资成本, 去化周期 |
| Food & bev / 食品饮料 | 动销, 库存, 提价能力 |
| Internet / 互联网 | DAU/MAU, ARPU, 收入增速, 利润率 |

### Policy Risk Framework

**High policy-sensitivity sectors (examples span markets):**
1. 医药 / Pharma — 招采/集采, 药价改革, drug pricing
2. 互联网 / Internet — 反垄断, 数据安全, antitrust
3. 半导体 / Semiconductors — 出口管制, 国产替代, export controls
4. 金融 / Financials — 监管周期, 利率政策
5. 房地产 / Real estate — 融资监管, 限购限贷
6. 新能源 / Clean energy — 补贴退坡, 关税, 产能过剩

**Policy risk assessment:**
- Current policy stance (支持/中性/限制)
- Upcoming regulatory events (反垄断、关税与出口管制、行业监管)
- Historical policy impact patterns
- Best/worst case scenarios

用 `search_documents`（行业/宏观/策略研报）与 `search_documents`(valuz-search) 拉取相关政策背景与新闻。

## Quality Checks

Before delivering:
- [ ] Industry definition clear and consistent
- [ ] Market scope (target market) stated; 符号格式正确（valuz-data MARKET:LOCAL 规范代码 AAPL / 00700 / 600519；valuz-search `market:ticker` US:AAPL / HK:00700 / SH:600519）
- [ ] Market size data sourced (`search_documents`(valuz-search))
- [ ] Competitive landscape covers top 5-10 players (`get_industries`/`get_index`(valuz-data))
- [ ] Financials sourced via `get_financial_statements`/`get_company`(valuz-data)
- [ ] Valuation multiples calculated consistently (`get_snapshots`(valuz-data))
- [ ] Policy environment analyzed (`search_documents`(valuz-search))
- [ ] Investment themes articulated
- [ ] Ideas shortlist included (3-5 names)
- [ ] Risk factors specific to the target market included
