---
name: idea-generation
description: Systematic stock screening and investment idea sourcing for global equity markets (focus US / HK / A-shares, also other markets). Combines quantitative screens, thematic research, and pattern recognition to surface new long and short ideas. Powered by valuz-data (quotes, financials, factor screening via screen_instruments, themes, and run_backtest) and valuz-search (categorized earnings, conference, research, filing, and news documents). Triggers on "选股", "股票筛选", "寻找机会", "stock screen", "stock ideas", "find ideas", or "screen for opportunities".
---

# idea-generation

## Purpose

Systematically surface new **全球股票市场（美股/港股/A 股为主，兼顾其他市场）投资机会** through quantitative screens, thematic analysis, and pattern recognition.

## Data Sources

Two MCP connectors cover the full sourcing workflow:

- `valuz-data` (Valuz Data MCP) — 行情、财务、指标、因子筛选与完整文档读取。
- `valuz-search` (Search MCP) — 财报、公告、研报、纪要、电话会发现。

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.

用 `valuz-data` 的因子引擎 (`list_factors` / `compute_factors` / `screen_instruments`) 做量化筛选，用 `valuz-search` 取定性催化/主题资料。

### Primary: valuz-data

```python
list_factors()                                → 列出可用因子 (PE/PB/ROE/RSI/MACD…)
compute_factors(symbol, formula)              → 算单只标的因子值, 验证公式
screen_instruments(market, formula)               → 因子选股 (核心), market=cn/hk/us
get_snapshots(symbol)                           → Current price and market activity
get_valuations(kind="latest", symbol)          → PE, PB, market cap
get_company(kind="profile", symbol)            → 公司概览
get_financial_statements(statement_type="income", symbol, period, limit)   → Income data
get_financial_statements(statement_type="balance", symbol, period, limit)  → Balance sheet
get_financial_statements(statement_type="cash_flow", symbol, period, limit) → Cash flow
get_financial_statements(statement_type="revenue_breakdown", symbol)       → 收入拆分
get_industries(kind="constituents", ...)       → Peer comparison
get_themes(kind="list")                        → 主题列表
get_bars(kind="bars", symbol)                  → Price trends, momentum
run_backtest(...)                                  → 策略回测
```

### Secondary Screening Data

| Data | Source | Use |
|------|--------|-----|
| Themes | valuz-data (`get_themes(kind="list"|"constituents")`) | Theme universe and constituents |
| Index / industry constituents | valuz-data (`get_index`, `get_industries`) | Universe definition, peer set |
| Shareholder structure | valuz-data (`get_ownership`) | Institutional accumulation/distribution |
| Earnings calendar | valuz-data (`get_calendar`) | Catalyst timing |
| Institutional / analyst views | valuz-search (`search_documents`) | Sell-side conviction, target prices |
| Filings & disclosures | valuz-search (`search_documents`) | Insider transactions, lock-up, M&A |

## Workflow

### Step 1: Define Screen Criteria

**Investment philosophy alignment:**
- Value vs Growth vs GARP vs Momentum
- Market cap preference (large / mid / small)
- Sector focus or sector-agnostic
- Liquidity requirements (turnover threshold)
- Risk tolerance (volatility, leverage, earnings stability)

**Screen parameters (market-aware, fields not tied to one market's data vendor):**

| Parameter | Typical Range | Notes |
|-----------|--------------|-------|
| PE (TTM) | 5-50x | Avoid negative PE |
| PB | 0.5-5x | <1x may indicate distress |
| PS | 0.5-5x | For high-growth unprofitable |
| Market cap | above liquidity threshold | Tradability |
| Daily turnover | above liquidity threshold | Tradability |
| ROE | >10% | Quality filter |
| Debt/Equity | <100% | Financial health |
| Revenue growth | >10% | Growth filter |
| EPS growth | >15% | Earnings momentum |

### Step 2: Quantitative Screens

筛选用 `valuz-data` 的 **`screen_instruments`** (核心)，传 `market` (`cn` / `hk` / `us`) + 因子 `formula`。
先用 `list_factors()` 看可用因子，必要时用 `compute_factors(symbol, formula)` 在单只标的上验证公式再放大到全市场。

> **Factor syntax** — 技术: `RSI(14)` / `MACD()` / `BOLL(20,2)` / `KDJ()` / `ATR(14)`；
> 基本面: `PE()` / `PE_TTM()` / `PB()` / `ROE()` / `ROA()` / `EPS()`；
> 财报字段: `INCOME.net_profit` / `BALANCESHEET.total_assets`；
> 组合用 `&` / `|`，如 `(PE()<20)&(ROE()>0.15)`. 跨市场重复同一筛选，分别传 `market="us"` / `"hk"` / `"cn"`.

**Screen 1: Deep Value (深度价值)**

```python
screen_instruments(market="us", formula="(PE_TTM()<15)&(PB()<1.5)&(ROE()>0.10)")
# 港股 / A 股: market="hk" / market="cn"
```

Output: Value candidates with potential mispricing.

**Screen 2: Growth at Reasonable Price (GARP)**

```python
screen_instruments(market="us", formula="(PE()<25)&(ROE()>0.15)&(EPS()>0)")
# PEG 思路: 用 compute_factors 验证 PE() 与盈利增速的比值，再以 PE 上限近似 GARP
```

Output: Quality growth at reasonable multiples.

**Screen 3: Momentum (趋势跟踪)**

```python
screen_instruments(market="us", formula="(RSI(14)>40)&(RSI(14)<70)&(MACD()>0)")
# 趋势确认可叠加均线/布林: 用 list_factors() 查可用价格因子, compute_factors 验证
```

Output: Momentum names in uptrends.

**Screen 4: Turnaround (困境反转)**

```python
# 量化部分: 当前承压但盈利转正/资产负债改善的标的
screen_instruments(market="cn", formula="(ROE()>0)&(PB()<1.5)")
# 定性催化 (新管理层 / 债务重组 / 订单回暖 / 内部人买入):
#   search_documents(category="all") (valuz-search), 用 market:ticker
```

Output: Potential turnaround candidates.

**Screen 5: Dividend Yield (高股息)**

```python
screen_instruments(market="hk", formula="(PB()<2)&(ROE()>0.10)")
# 股息率/派息率/FCF 用 get_financial_statements(statement_type=income|balance|cash_flow) 核验 (valuz-data, MARKET:LOCAL 规范代码)
```

Output: High-quality income names.

**Screen 6: Special Situations (事件驱动)**

```python
# 事件驱动以检索为主, 用 valuz-search (market:ticker):
#   search_documents  → 限售解禁 / M&A / 重组公告
#   search_documents     → 集采结果、指数纳入/剔除、监管催化
#   search_documents  → 卖方对事件的解读
# get_calendar (valuz-data) 锁定业绩披露时点
```

Output: Event-driven opportunities.

### Step 3: Thematic Research

**Identify emerging themes:**

| Theme Type | Examples | Data Sources |
|-----------|-------------------|-------------|
| Policy-driven | 国产替代, 新基建, 双碳, reshoring | `search_documents` (valuz-search) |
| Technology | AI应用, 自动驾驶, 机器人 | `search_documents` (valuz-search) |
| Demographics | 老龄化, 少子化 | `search_documents` (valuz-search) |
| Consumption | 消费升级, 国货崛起 | `search_documents` (valuz-search) |
| Industrial | 高端制造, 专精特新 | `search_documents` (valuz-search) |

**Thematic screening approach:**
1. Define theme and investable universe — `get_themes(kind="list")` then `get_themes(kind="constituents")`; use `get_industries(kind="constituents")` for an industry universe
2. Map companies (US / HK / A-share and beyond) to theme — 用 `search_documents` (valuz-search) 佐证个股的主题表达度
3. Rank by exposure and quality — 叠加 `screen_instruments(market=..., formula=...)` 在主题股池内做质量过滤
4. Identify pure-plays vs beneficiaries — `get_financial_statements(statement_type="revenue_breakdown", symbol)` 看收入对主题的纯度

### Step 4: Technical Analysis

**Technical considerations** (factor 语法可直接进 `screen_instruments` / `compute_factors`，原始价量用 `get_bars` / `get_bars`):

| Indicator | Factor / Tool | Use |
|-----------|---------------|-----|
| 均线 (Moving averages) | `BOLL(20,2)` + `get_bars(kind="bars", symbol)` | Trend direction (5/10/20/60/120 day) |
| MACD | `MACD()` | Momentum and trend changes |
| RSI | `RSI(14)` | Overbought/oversold |
| KDJ | `KDJ()` | Short-term momentum |
| 成交量 (Volume) | `get_bars(kind="bars", symbol)` | Confirmation of moves |
| 波动率 (Volatility) | `ATR(14)` | Risk sizing, breakout strength |
| 概念热度 (Theme heat) | `get_themes()` | Where the money is rotating |

**Chart patterns to watch:**
- 突破 (breakout) — above resistance
- 回踩 (pullback to support) — entry opportunity
- 双底/头肩 (double bottom/head & shoulders) — reversal signals
- 量价背离 (volume-price divergence) — trend exhaustion

### Step 5: Fundamental Deep Dive

**For each candidate** (核验用 valuz-data MARKET:LOCAL 规范代码 + valuz-search `market:ticker`):

1. **Business model review**: How does company make money? — `get_company(kind="profile", symbol)`, `get_financial_statements(statement_type="revenue_breakdown", symbol)`
2. **Financial health**: Balance sheet, cash flow, earnings quality — `get_financial_statements(statement_type="balance"|"cash_flow", symbol, period, limit)`
3. **Competitive position**: Market share, moat, pricing power — `get_industries(kind="constituents", ...)` + `search_documents(category="research_reports")`
4. **Management quality**: Track record, capital allocation — `search_documents` (valuz-search)
5. **Valuation**: vs peers, vs history, vs international peers — `get_snapshots(symbol)` + `compute_factors(symbol, "PE_TTM()")` / `PB()`
6. **Catalyst**: What could re-rate the stock? — `search_documents` (valuz-search), `get_calendar` (valuz-data)

**Red flag checklist:**
- 商誉占比过高 (>30% of equity)
- 应收账款增速 > 收入增速
- 经营现金流持续为负
- 大股东质押比例过高 (>50%)
- 审计意见非标准无保留
- 频繁变更会计师事务所
- 关联交易占比高

### Step 6: Build the Ideas List

**Standard format:**

| Rank | Ticker | Company | Sector | Idea Type | Thesis | Catalyst | Risk | Conviction |
|------|--------|---------|-------|-----------|--------|----------|------|------------|
| {{RANK}} | {{TICKER}} | {{COMPANY_NAME}} | {{SECTOR}} | {{DIRECTION}} | {{THESIS}} | {{CATALYST}} | {{RISK}} | {{CONVICTION}} |
| Example | AAPL | Apple | Tech | Long | Services mix shift + buybacks | Earnings beat | Demand slowdown | High |
| 2 | 0700.HK | Tencent | Internet | Long | Game recovery + ad growth | New title approvals | Regulatory | Medium |
| 3 | 600519.SH | 贵州茅台 | 白酒 | Long | 批价稳+动销旺+分红高 | Q1业绩超预期 | 批价下行 | High |

Tickers span markets — US (`AAPL`), HK (`0700.HK`), A-share (`600519.SH`), and others.

**Conviction levels:**
- **High**: Strong thesis, clear catalyst, limited downside
- **Medium**: Good thesis, catalyst timeline uncertain
- **Low**: Exploratory, needs more research

### Step 7: Monitor & Update

**Ongoing tracking:**
- Weekly price and news updates — `get_snapshots(symbol)` (valuz-data), `search_documents` (valuz-search)
- Catalyst tracking — `get_calendar` (valuz-data), `search_documents` (valuz-search)
- Thesis validation / invalidation — `search_documents` (valuz-search), `compute_factors(symbol, formula)` to re-check the screen metrics
- Position sizing recommendations

**Update triggers:**
- Earnings results
- Material news (M&A, guidance, regulation)
- Price moves >15% in a week
- Thesis breaking or confirming

## Market-Specific Screening Considerations

### Market Structure

| Feature | Screening Implication |
|---------|----------------------|
| 涨跌停限制 (price limits, where they apply) | Momentum may be interrupted |
| 散户占比 (retail participation) | Sentiment-driven overreactions more common in retail-heavy markets |
| 政策敏感 (policy sensitivity) | Regulatory risk premium in certain sectors |
| Cross-border fund flows | Track foreign flows for large-caps (`get_ownership` for ownership shifts) |
| 概念轮动 (Concept rotation) | `get_themes` / `get_themes` signal sentiment shifts |
| 停牌 (Trading halt) | Due diligence risk for suspended names |

### Common Investment Styles

| Style | Description | Key Metrics |
|-------|-------------|-------------|
| 价值投资 | Deep value, dividend, asset-based | PB, dividend yield, 破净 |
| 成长投资 | High growth, innovation | Revenue growth, R&D intensity |
| 主题投资 | Policy/trend themes | Catalyst proximity, theme purity |
| 技术分析 | Chart-based trading | 均线, MACD, 量价 |
| 量化策略 | Systematic, factor-based | Multi-factor models |
| 打新 | IPO subscription | 中签率, 涨幅预期 |

### Sector-Specific Screening

| Sector | Screening Focus |
|--------|----------------|
| 消费/必选 | 批价趋势, 渠道库存, 回款, 品牌力 |
| 半导体 | 产能利用率, 国产替代进度, 技术迭代 |
| 新能源 | 产能过剩/出清, 技术路线, 补贴退坡影响 |
| 医药 | 创新药管线, 集采中标, 国际化 |
| 银行 | NIM趋势, 不良率, 拨备, 估值 (破净) |
| 房地产 | 销售额, 融资能力, 土储质量 |
| 消费 | 动销, 库存, 消费升级/降级趋势 |

## Quality Checks

Before delivering ideas list:
- [ ] Screen criteria documented and reproducible
- [ ] Each candidate has fundamental backing
- [ ] Catalysts identified for each idea
- [ ] Risk factors clearly stated
- [ ] Conviction levels assigned
- [ ] Liquidity verified (tradable)
- [ ] Regulatory/compliance review passed
