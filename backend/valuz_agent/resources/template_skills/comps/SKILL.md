---
name: comps
description: Comparable company analysis for global equities (focus US / HK / A-shares, also other markets). Uses the Valuz Quotes MCP (valuz-data) and Valuz Search MCP (valuz-search) to build cross-market peer groups, pull financial data, compute valuation multiples (PE, PB, PS), and assess relative value within an industry sector.
---

# comps

## Data Sources

全球股票市场（美股/港股/A 股为主，兼顾其他市场）的可比公司分析使用两个 Valuz 连接器：

> **代码格式**：两个连接器都用规范的 `MARKET:LOCAL` 代码；非规范输入先调用 `resolve_symbols`。

### `valuz-data` — Valuz Data MCP
行情、财务报表、估值因子、行业成分。可比分析常用：

```text
get_industries(kind="constituents", ...) → 同业（行业成分股）
get_index(kind="constituents", ...)      → 指数成分股（备选同业来源）
get_company(kind="profile", symbol)      → 公司画像、规模、业务描述
get_snapshots(symbol)                     → 当前价格
get_valuations(kind="latest", symbol)    → 市值与估值倍数
compute_factors(symbols=[...], ...)              → PE()/PB()/PS()/ROE()/EPS() 估值倍数
get_financial_statements(statement_type="income", symbol, period="annual") → 营收、净利润
get_financial_statements(statement_type="balance", symbol, period="annual") → 账面价值、负债
```

### `valuz-search` — Valuz Search MCP
财报、公告、研报、纪要、电话会检索。可比分析主要用 `search_documents` 取行业研报做定性对照。

```text
search_documents(category="all", query=..., symbols=["US:AAPL", ...])   → 行业研报 / 同业定性对照
search_documents(category="all", query=...)                       → 综合检索（财报/纪要/公告/新闻）
```

> 取数原则：用 `valuz-data` 取行情/财务/倍数与同业成分，用 `valuz-search`（`search_documents`）取定性研报对照。

---

# comps

## Workflow

### 1. Define the peer group

Start with the target stock, then use `get_industries`（valuz-data，MARKET:LOCAL 规范代码如 `600519`）to retrieve industry peers for that sector — or `get_index`（valuz-data）when the peer set is better anchored to an index. Peer sets should be **cross-market** — a peer group can mix US, HK, and A-share listings within the same industry. Common sectors and example leaders:

| Industry | Example Leaders |
|----------|-----------------|
| 白酒 / Spirits | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 半导体 / Semiconductors | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 电池 / Batteries | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 银行 / Banks | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 证券 / Brokers | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 保险 / Insurance | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 医疗器械 / Medical Devices | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 光伏设备 / Solar | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 汽车整车 / Autos | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 软件开发 / Software | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |

Use canonical symbols — US (`US:AAPL`), HK (`HK:00700`), A-share (`SH:600519`).

### 2. Pull financial data for each peer

代码格式提醒：valuz-data 用MARKET:LOCAL 规范代码（`US:AAPL` / `HK:00700` / `SH:600519`），valuz-search 用 `market:ticker`（`US:AAPL` / `HK:00700` / `SH:600519`）。

```text
For the peer set as a whole:
  compute_factors(symbols=[...], ...)  → PE()/PB()/PS()/ROE()/EPS() 倍数（批量）   (valuz-data)

For each ticker in the peer set:
  get_snapshots(symbol)                              → price                         (valuz-data)
  get_valuations(kind="latest", symbol)             → market cap and multiples      (valuz-data)
  get_company(kind="profile", symbol)               → business description          (valuz-data)
  get_financial_statements(statement_type="income", symbol, period="annual") → revenue, net income
  get_financial_statements(statement_type="balance", symbol, period="annual") → book value, debt
  search_documents(category="all", query="...", symbols=["US:AAPL", ...]) → qualitative color (行业研报) (valuz-search)
```

### 3. Compute standard multiples

倍数优先用 `compute_factors`（valuz-data）批量计算，因子语法用 `PE()` / `PB()` / `PS()` / `ROE()` / `EPS()`；无现成因子时再用 `get_snapshots` + 报表口径自行换算。

| Multiple | Formula | valuz-data source |
|----------|---------|--------------------|
| PE (TTM) | Price / EPS TTM | `compute_factors` → `PE()`（或 `PE_TTM()`） |
| PB | Price / Book Value per share | `compute_factors` → `PB()` |
| PS (TTM) | Market Cap / Revenue TTM | `compute_factors` → `PS()`（或由 `get_valuations` 市值 + `get_financial_statements` 营收换算） |
| EV/EBITDA | Enterprise Value / EBITDA | 由 `get_valuations` 市值 + `get_financial_statements` 负债/现金换算 |
| ROE | Net Income / Equity | `compute_factors` → `ROE()` |
| Dividend Yield | DPS / Price | `get_valuations(kind="latest")` → yield |

### 4. Present the comps table

Sort by market cap (largest first). Flag outliers (>2 standard deviations from mean). Include:
- Ticker, company name, price
- Market cap (in the listing's local currency, e.g. USD / HKD / CNY)
- PE, PB, PS
- Revenue growth %, Net margin %
- 52-week high/low

### 5. Relative value assessment

- If target's PE is >1 std dev above peer mean → potentially overvalued
- If target's PE is >1 std dev below peer mean → potentially undervalued
- Flag companies with negative earnings separately
- Compare PEG ratios (PE / growth rate) when growth data is available

## Notes

- Financial data may be reported under US GAAP / IFRS / CAS depending on the listing — normalize when comparing across markets (按当地准则口径)
- Revenue is reported 按当地准则口径; reconcile definitions before comparing cross-market peers
- Some markets apply daily price-limit mechanisms (e.g. A-shares ±10% main board / ±20% ChiNext/STAR) — note these when interpreting single-day moves
- For A-share names, market cap may be quoted as 流通市值 (circulating) or 总市值 (total) — confirm which the user wants
- For cross-market comps (e.g. A-share vs HK-listed dual-listings), note that A-shares typically trade at a premium
