---
name: morning-note
description: Daily morning research note for global equity markets. Summarizes overnight developments, pre-market sentiment, trade ideas, and key events for the trading day across US, HK, and A-share markets (and other markets where relevant). Uses valuz-data for quotes, financials, and indicators, and valuz-search for earnings reports, calls, research, minutes, filings, and news. Triggers on "早报", "晨会纪要", "今日策略", "morning note", "morning meeting", or "daily market preview".
---

# morning-note

## Purpose

Draft concise **晨会纪要/每日策略 (morning meeting note / daily strategy)**, summarizing overnight developments and pre-market sentiment for global equity markets — focusing on US, HK, and A-share markets, while also covering other markets where relevant. Designed for the typical sell-side 晨会 format (run before the local market open, e.g. 7:00-8:30 AM).

## Data Sources

> **Symbol format:** `valuz-search` and `valuz-data` both use canonical `MARKET:LOCAL` symbols (`US:AAPL` / `HK:00700` / `SH:600519`). Call `resolve_symbols` first for aliases or non-canonical input. Search on `valuz-search`; read selected documents and all structured data on `valuz-data`.

### valuz-data — 行情 / 指数 / 概念热度 / 财报日程 (quotes, indices, concept heat, earnings calendar)

Use `valuz-data` for structured market data: real-time quotes, index levels, intraday concept (theme) heat, and the day's earnings calendar. Covers US, HK, and A-share tickers (bare code, e.g. `AAPL`, `00700`, `600519`).

```
valuz-data get_snapshots(symbol)          → Latest price, change, volume for a ticker
valuz-data get_snapshots(symbol)          → Index level and change
valuz-data get_themes(...)          → Today's hot concepts / themes
valuz-data get_themes(...)         → Latest concept-heat ranking
valuz-data get_calendar(...)       → Companies reporting today
```

### valuz-search — 要闻 / 公告 / 研报 / 财报检索 (news, filings, research, earnings)

Use `valuz-search` for unstructured research retrieval: overnight/pre-market news, regulatory filings and announcements, sell-side research, and earnings materials. Pass `market:ticker` symbols (e.g. `US:AAPL`, `HK:00700`, `SH:600519`) via `symbols[]`, plus `query` and optional `start_datetime` / `end_datetime`.

```
valuz-search search_documents(category="all", query, symbols[])      → Market and stock-specific headlines
valuz-search search_documents(category="all", query, symbols[])   → Regulatory filings and announcements
valuz-search search_documents(category="all", query, symbols[])   → Sell-side research and analysis
valuz-search search_documents(category="all", query, symbols[])  → Earnings reports / calls / transcripts
```

### Macro Data (if relevant)

| Data | Market | Frequency |
|------|--------|-----------|
| PMI (制造业/非制造业, manufacturing/services) | US / China / global | Monthly |
| CPI / PPI | US / China / global | Monthly |
| Rate decisions (Fed / PBoC / other central banks) | global | Per meeting |
| FX rates (USD/CNY, USD/HKD, DXY) | global | Daily |
| Bond yields (10Y UST, 10Y CGB) | US / China | Daily |
| Cross-border flows (北向资金 Northbound, 南向资金 Southbound) | A / HK | Daily |

## Workflow

### Step 1: Overnight Developments

> 要闻/公告/研报用 `search_documents`(valuz-search)，按 `query` + `symbols[]`（`market:ticker`）检索，必要时加 `start_datetime`/`end_datetime` 限定隔夜区间。

**Macro / Policy:**
- Central bank announcements — Fed (FOMC, rate decisions), PBoC (MLF/LPR rates, RRR cuts, open market operations), and other central banks
- Government / regulator policy — industry regulations, 产业政策, sector rule changes
- Overnight markets — US equities, Asia/Europe sessions, commodities, FX
- Cross-market read-through — how overnight US/HK action affects the next open

**Company news:**
- Earnings releases from prior evening / overnight (across US, HK, A-share)
- 业绩预告 (earnings preview / guidance notices)
- 增减持 (shareholder buy/sell announcements)
- 重大合同 (major contract wins)
- 回购 (share buybacks)

**Format:**
```
【宏观/政策 Macro / Policy】
- [News item with brief implication]
- [News item with brief implication]

【公司要闻 Company News】
- [Company]: [Event] — [Implication]
- [Company]: [Event] — [Implication]
```

### Step 2: Market Preview

> 行情用 `get_snapshots`（个股）/ `get_snapshots`（指数）(valuz-data，MARKET:LOCAL 规范代码)；板块/概念热度用 `get_themes` 或 `get_themes`(valuz-data)。

**Previous session recap (per relevant market):**
- US: S&P 500, Nasdaq, Dow — close, change, volume
- HK: Hang Seng, HSTECH — close, change
- A-share: 上证指数, 深证成指, 创业板指 — close, change, 涨跌家数
- Sector performance: top 3 up/down sectors
- Cross-border flows: 北向资金 (Northbound) / 南向资金 (Southbound) net buy/sell, direction

**Overnight markets:**
- US three major indices (Dow, Nasdaq, S&P 500) — read-through to the next open
- Hang Seng / HK futures
- Commodities: 原油 (crude), 铜 (copper), 黄金 (gold)
- FX: USD/CNY, USD/HKD, DXY movement

### Step 3: Trade Ideas

**2-4 actionable ideas for the day:**

For each idea:
- **方向 Direction**: Long / Short / Watch
- **标的 Name**: Company name + ticker (e.g. `AAPL`, `00700`, `600519`)
- **逻辑 Thesis**: Brief thesis (1-2 sentences)
- **催化剂 Catalyst**: Key event to watch
- **风险 Risk**: Main risk factor

**Example format:**
```
【今日策略 Today's Strategy】
1. [方向] [标的]（[代码]）
   逻辑：[简要投资逻辑]
   催化剂：[近期催化剂]
   风险：[主要风险]

2. ...
```

### Step 4: Key Events Calendar

> 当日财报日程用 `get_calendar`(valuz-data)；相关公告/说明会用 `search_documents`(valuz-search，`symbols[]` 取 `market:ticker`)。

**Today's events:**
- Earnings releases (US / HK / A-share companies reporting)
- Economic data releases (CPI, PMI, payrolls, etc.)
- Policy events (FOMC, PBoC MLF operations, central bank briefings)
- Earnings calls (业绩说明会 / earnings calls)
- Sector conferences / 策略会

### Step 5: Sector / Thematic Focus

**Sector of the day:**
- What's moving?
- Key drivers (policy, data, sentiment)
- Names to watch within sector

### Step 6: Draft the Note

**Standard morning note format:**

```
【XX Research】晨会纪要 Morning Note [YYYY-MM-DD]

一、市场回顾 Market Recap
   [Yesterday's close per market, sector performance, cross-border flows]

二、隔夜外盘与大宗 Overnight Markets & Commodities
   [Overnight US/Asia/Europe markets, commodities, FX]

三、重要资讯 Key News
   【宏观政策 Macro / Policy】...
   【公司要闻 Company News】...
   【行业动态 Sector Moves】...

四、今日策略 Today's Strategy
   [Trade ideas with rationale]

五、重点事件日历 Key Events Calendar
   [Today's scheduled events]

六、风险提示 Risk Notes
   [Market-wide risks]
```

### Step 7: Delivery

**Tone guidelines:**
- Concise and actionable
- Opinionated but evidence-based
- Flag high-conviction ideas vs. lower-conviction
- Include risk management notes (止损位 stop levels, 仓位建议 sizing suggestions)

**Typical length:**
- 300-600 words for daily note
- 800-1,500 words for weekly strategy piece

## Market-Aware Morning Context

### Market Mechanics

| Item | US | HK | A-share |
|------|----|----|---------|
| Pre-market | Pre-market session (e.g. 4:00-9:30 ET) | Pre-opening auction | No formal pre-market; some dark pools |
| Opening | 9:30 AM ET | 9:30 AM HKT | 9:30 AM (集合竞价 9:15-9:25) |
| Closing | 4:00 PM ET | 4:00 PM HKT | 3:00 PM (集合竞价 14:57-15:00) |
| Lunch break | None | None | 11:30 AM - 1:00 PM |
| Trading limits | None (circuit breakers index-level) | None | ±10% (main), ±20% (创业板/科创板), ±30% (ST) |

### Common Cross-Market Themes

- **政策市 (policy-driven moves)** — government/central-bank announcements move markets (esp. A-share)
- **存量博弈 (capital rotation)** — limited new money, sector rotation
- **北向资金 / 南向资金 (Northbound / Southbound flows)** — tracked as sentiment indicators for A / HK
- **两融余额 (margin balances)** — leverage indicator
- **龙虎榜 (Dragon-Tiger list)** — unusual activity names (A-share)
- **涨跌停 (limit-up/limit-down)** — A-share momentum; gaps and halts for US/HK

### Common Catalysts

- Central bank operations (Fed FOMC, PBoC open market operations)
- Rate announcements (Fed funds, LPR报价 / Loan Prime Rate)
- PMI数据 (monthly PMI releases, US ISM / China PMI)
- Earnings windows (US quarterly season; A-share 季报/年报 windows: Apr, Aug, Oct)
- Major meetings (FOMC, 政治局会议, 中央经济工作会议)
- 行业监管政策 (sector regulation: antitrust, procurement, etc.)
- Cross-border flows (foreign inflow via 沪深港通 Stock Connect)

## Quality Checks

Before delivering:
- [ ] Market data current and accurate (across relevant markets)
- [ ] Overnight developments captured
- [ ] Trade ideas actionable and time-relevant
- [ ] Event calendar complete
- [ ] Risk factors included
- [ ] Tone appropriate for morning meeting format
