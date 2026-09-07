# Valuz Design Spec v2.7

> 2026-06-15 · 替代 `frontend/docs/design/DESIGN.md`（v1）
> 配套文件：[tokens.css](tokens.css)（可直接替换 project.css 的 token 段）· [spec.html](spec.html)（可视化规范页）· [components.html](components.html)（十七个高频组件）· [design-audit.html](design-audit.html)（一致性检测器）
> 本文档锚定 **`packages/ui` 真实组件代码**，不再锚定原型 `app.jsx`。

---

## 0. 裁决记录

v1 时期 Figma 色板、DESIGN.md、代码 token 三方互相矛盾。以下裁决为最终值，三端（Figma Variables / 本文档 / tokens.css）同步生效：

| 项 | 裁决值 | 废止值 | 说明 |
|---|---|---|---|
| 品牌主色 | **#725cf9** | #6d5cff、#533afd、#965cf9 | Figma 色板钦定主色；代码端改 `--brand` 一行全局生效 |
| Warning 橙 | **#ef8b0c**（v2.4） | #ff8710、#d97706 | 三件套架构后基色卸下文字职责，按设计反馈提亮去棕褐感；文字场景一律用派生的 warning-text |
| 状态绿 | **#16a34a** | — | 连接成功、完成态、checkmark |
| 财务绿（跌） | **#53bc76** | **#53cb76** | #53cb76 确认为 Figma 色板标签笔误（b/c 颠倒），代码中 9 处需替换 |
| 财务红（涨） | **#f54b4b** | — | 与 error #dc2626 语义分离，不得混用 |
| Accent 粉 | **#ec4899** | #ef5da8 | 色板色块误填，以标签与代码为准 |
| Error 红 | **#e5484d** | #dc2626 | 相对亮度略高于品牌紫（≈0.22 vs 0.18）；白字对比度 **≈3.9:1**——达 AA Large / UI 组件（3:1），**未达 AA 正文（4.5:1）**。**裁决（v2.6.10）**：error 本体保留用于 soft/border/text 与图标圆底（图标按 UI 3:1 达标）；**「实心红底 + 白字」的文字场景（destructive 按钮）改用派生的 `error-strong`**（error 混 12% 黑，白字 ≈5.3:1 / 暗色 ≈5.1，过正文 AA）。不再压深 error 本体以免连带三件套与暗色位移 |
| Disabled 文字 | **fg-30（实测 #b1b5ba）** | #e6e7e9 | v1 值对比度 1.3:1 不可读；划线完成态仍可用 fg-30+line-through（#b6b7bc 是 v1 旧 ink-muted 近似，渲染真值见 §2.2） |

---

## 1. 架构原则

```
原语层（手选）   每个模式只允许 18 个手选颜色：9 基色 + 1 蓝灰极 + 8 点缀色
    ↓ color-mix 派生
语义层（派生）   灰阶 fg-1~80、状态三件套、品牌色阶、阴影
    ↓ 引用
组件层           button/popover/card… 只引用语义层，禁止引用原语或字面量
```

**三条铁律：**
1. 任何新颜色必须能回答"从哪个基色派生"。答不上来 = 不准进代码。
2. 同语义必须同 token：次级文字只有 `ink-body`（即 fg-60）一个名字。
3. 新组件先查 `packages/ui/components/ui/`，第二次出现的样式组合必须上提为组件。

---

## 2. 颜色

### 2.1 基色（唯一手选区）

| Token | Light | Dark | 用途 |
|---|---|---|---|
| `--background` | `#f8f9fb` | `#0f1012` | 页面大背景 |
| `--foreground` | `#131313` | `#e4e4e7` | 一级文字、灰阶派生源 |
| `--surface` | `#ffffff` | `#17181c` | 面板、卡片、弹层 |
| `--brand` | `#725cf9` | `#8b7afc` | 主 CTA、品牌强调、focus ring、Info |
| `--success` | `#16a34a` | `#22c55e` | 状态绿 |
| `--warning` | `#ef8b0c` | `#f59e0b` | 警示（实底/图标须配文字，独立传达信息用 warning-text） |
| `--error` | `#e5484d` | `#ef4444` | 错误、危险操作 |
| `--finance-up` | `#f54b4b` | `#ff6b6b` | 涨（仅财务数据） |
| `--finance-down` | `#53bc76` | `#5fd389` | 跌（仅财务数据） |
| `--slate` | `#444b54` | `#a8b1bf` | 蓝灰极（=v1 neutral/700），灰阶中段的派生轴 |

点缀/图表分类色（8 个，暗色用对应 400 档）：
`1 sky #0ea5e9 · 2 teal #14b8a6 · 3 amber #eab308 · 4 pink #ec4899 · 5 blue #3b82f6 · 6 lime #84cc16 · 7 orange #f97316 · 8 fuchsia #d946ef`
规则：图表系列严格按此顺序取色（保证跨页面同一序号同色）；超过 8 个系列用 `fg-50` 归并为"其他"；amber 是点缀不得当 warning 用；扩展时同亮度饱和度只动色相。

### 2.2 灰阶（派生，禁止手选）

双轴结构：浅端（1~12）沿 foreground 派生保持中性；中段（30~80）沿蓝灰极 `slate` 派生——冷调在中间最饱满、两端归零，与 v1 色板的冷灰气质一致。档位名表示深度，不等于混合百分比。

| Token | 配方 | 实测渲染值 | 用途 |
|---|---|---|---|
| `fg-1` | fg 1% → bg | `#f5f6f8` | hover 底、工具卡片底 |
| `fg-2` | fg 2% | — | List Item 选中态 |
| `fg-3` | fg 3% | `#f0f1f3` | 分割线、内卡边界 |
| `fg-5` | fg 5% | `#ebecee` | 次级浅底 |
| `fg-8` | fg 8% | `#e3e4e6` | 常规边框 |
| `fg-12` | fg 12% | `#d9d9db` | 强边界 |
| `fg-30` | slate 37% → bg | `#b1b5ba` | 弱辅助文字、disabled |
| `fg-50` | slate 60% | `#878c93` | 图标次级（v1 漏收，曾被硬编码） |
| `fg-60` | slate 75% | `#6d737b` | **次级文字唯一 token**（ink-body） |
| `fg-80` | = slate | `#444b54` | 强次级（v1 漏收） |

> 注：上表为 `color-mix(in oklab,…)` 的实测渲染值，非 v1 旧色板值——旧灰阶仅作迁移来源，对照见 §9 codemod。检测器 `design-audit.html` 的内置标准色与此列一致。

### 2.3 状态色三件套

每个状态色固定三个派生角色，**禁止手配浅底/深字**：

```
X-soft   = mix(X 10%, background)   浅底（badge、inline alert 底）
X-border = mix(X 35%, background)   边框
X-text   = mix(X 65%, foreground)   该底色上的文字
```

Info 复用品牌紫（裁决：不引入第五个状态色相）。

**财务涨跌色也有「文字档」**（`finance-up-text` / `finance-down-text` = mix 65% foreground）：基色 `finance-up #f54b4b` / `finance-down #53bc76` 太亮，作 ≤14px 数字时白底仅 ≈3.5:1 / ≈2.4:1（均未达 AA，绿色连 3:1 都不到）。规则：**涨跌幅、表格数字等 ≤14px 文字一律用文字档**；基色本体只留给 ≥18px 大数字、图表、实底圆点等图形场景。与状态三件套同理，禁止手配。

**warning 不做白字实心图标**：`warning #ef8b0c` 白字仅 ≈2.5:1（连 UI 3:1 都不到）。Toast 状态点缀只用 success/error/brand 实心圆 + 白字形；warning 的浮层提示走 `warning-soft` 圆底 + `warning-text` 字形，不要白字压在橙底上。

**Toast 是例外，刻意不套三件套**：浮层通知容器保持中性 `surface` 白底（仅 fg-8 边框 + shadow-3），只用基色实心圆图标（`background: var(--success)` 等基色本体 + 白色线性字形）做状态点缀——多条 Toast 堆叠时若整块染语义色会互相干扰。分工固定为：**"整块即该状态"的元素**（badge / inline alert / callout）用三件套；**浮层通知 Toast** 用中性底 + 基色实心圆图标。两者图标字形仍取同一套 Lucide 线性图标，只是封装不同。

### 2.4 品牌色阶

`50 #f3f2ff · 100 #eae6ff · 200 #d1c8ff · 300 #b29ff7 · 500 #725cf9 · 600 #5d46e8(hover) · 700 #4936c2(active/深字)`
渐变统一为 `--brand-gradient`（600→300），废止手写 `#533afd→#965cf9`。

---

## 3. 字体排印

- 主字体 `PingFang SC`；等宽 `ui-monospace/SF Mono/Menlo`；`Newsreader + Noto Serif SC` 仅限 onboarding 大标题。
- 注意：PingFang 无真斜体、字重档只有 Regular/Medium/Semibold 可用——**禁用 italic 和 700 以上字重**（700 仅限 ≤10px badge 的 Latin/数字）。

### 字阶（8 档整数，0.5px 档全部废止）

| Token | 字号 | 行高 | 字重 | 用途 |
|---|---|---|---|---|
| `micro` | 10px | 1.2 | 600/700 | 文件 badge、极小标识（仅图形场景，不做正文） |
| `2xs` | 11px | 1.4 | 400/600 | Section Label、表头、状态标签 |
| `xs` | 12px | 1.5 | 400/500 | meta、按钮标签、inline code、菜单副文案 |
| `sm` | 13px | 1.55 | 400/500 | Sidebar 行、Composer、列表主文案、菜单主文案 |
| `base` | 14px | 1.7 | 400/500 | **消息正文**、标题栏标题、页面基准 |
| `lg` | 15px | 1.5 | 500 | 右侧面板主标题 |
| `xl` | 18px | 1.4 | 500/600 | 页面标题 |
| `2xl` | 24px | 1.3 | 500 | onboarding、大数字 |

字重：400 正文 / 500 标题、关键字段 / 600 Label、强调 / 700 仅 micro badge。

---

## 4. 间距 · 圆角 · 阴影 · 层级 · 动效

- **间距**：4px 网格 `4 / 8 / 12 / 16 / 20 / 24 / 32`（v1 的 6/9/10/14/28 就近归档）。
- **圆角**：`sm 4 / md 6 / lg 8 / xl 10 / 2xl 12 / full`（2/3/7/14px 废止）。同层级容器共享同一档。
- **阴影**：从前景色派生（暗色自动加深）。**是否带 1px 环，取决于元素自身有没有边框**（v2.6 分层裁决，非"合并"）：
  - `shadow-outline` 纯投影**无环** → **自带边框**的元素与容器（outline 按钮、input，以及带 `border` 的卡片/列表/表单块）。环 + 边框会叠成双描边，故去环。
  - `shadow-1` 投影 + `fg-3` 浅环 → **无边框**容器（如设置卡 `.set-card`），用环代替边框、更立体。
  - `shadow-2` 悬浮卡片/sidebar active · `shadow-3` popover/dropdown · `shadow-4` modal/浮层/应用外壳。
  - 铁律：**有边框用 `shadow-outline`，无边框用 `shadow-1`**（v2.6.3 印证）。同一元素不得同时给 `border` 和 `shadow-1`。
- **z-index**：`base 0 · sticky 20 · titlebar 40 · panel 50 · dropdown 100 · tooltip 150 · modal 200 · toast 300`。
- **动效**：`fast 120ms`（hover）· `base 200ms`（折叠/滑入）· `slow 250ms`（布局），easing 统一 `cubic-bezier(0.4,0,0.2,1)`。

## 5. 图标

- Lucide 风格，viewBox 24，round cap/join。
- **stroke 只有 2 档**：默认 `2`，≥18px 大图标 `1.5`（v1 的 1.9/1.8/1.7/1.6 与母版残留的 2.4/2.6 一律**按渲染尺寸归位**：<18px→`2`、≥18px→`1.5`；未选中 checkbox 的 1 归 1.5）。母版 component/spec 已对齐,无第三档。
- 尺寸 3 档：`12 / 14 / 16`。
- 颜色：一级 `foreground`，二级 `fg-60`，折叠箭头 `fg-50`；同一行 ≤2 种图标色；普通图标禁用品牌色。

## 6. 无障碍底线

- 正文/标签文字 ≥ AA（4.5:1）：`foreground`、`fg-60`、`fg-80`、各 `X-text`、`finance-up-text`/`finance-down-text` 达标。
- **finance 基色（`finance-up`/`finance-down`）作 ≤14px 文字未达 AA**——小字涨跌一律用 `finance-*-text`，基色仅 ≥18px/图形（见 §2.3）。
- `fg-50` 仅限 ≥18px 或图标（含 placeholder 应 ≥fg-60，fg-50 在白底仅 3.4:1）；`fg-30` 仅限 disabled 与已完成划线，不承载必读信息。
- **小字徽标（≤12px）注意 fg-60 的临界**：`fg-60` 在 surface(4.8)/bg(4.5) 达标，但叠在 `fg-5` 灰底上仅 4.05:1——故灰底必读 pill（如 queued）文字用 `fg-80`，`tag-neutral` 同理。
- **accent 角色标签**仅作类目区分（非必读）；其中 amber 最亮，文字档已降到 50%（5.4:1）以过 AA，其余色 62% 即达标。
- **两种 focus 视觉，职责不同，不要混为一谈**：
  - `focus-visible`（键盘可达性指示）：所有可交互元素 `2px var(--brand)` ring + `2px offset`，禁止 `outline: none` 裸奔。用于 Tab 导航高亮，**对所有组件统一**。
  - Input **active focus**（字段正被输入时的状态）：边框转 `brand` + `0 0 0 3px` 的 `brand 20%` 柔和内环（不带 offset）。这是「当前活跃字段」的视觉，与上面的键盘焦点环是两件事，可以共存（shadcn 等同款做法）。两者都保留，不要为了「统一」把 Input 改成硬 ring——那会损 UX。

## 7. 组件层对齐规则

- 设计稿变体名 = 代码 props 名，逐字一致：Button 为 `default(主紫) / outline(次要) / ghost / destructive / link` × `sm / default / icon`（size 以 cva 现有三档为准；如代码后续新增 xs/lg，须先落地母版再写入本节，不在文档预留未实现档位）。Figma 现有 Primary/Secondary 命名按此重命名。
- **`secondary` 变体废止，并入 `outline`**（裁决：一个强调层级只配一个变体，消除"用哪个"的歧义；代码实测 outline 102 处 vs secondary 4 处，outline 已是事实上的次要按钮，且在灰底面板上白底+边框比灰填充更清晰）。迁移：cva 里将 `secondary` 设为 `outline` 的 deprecated 别名 → 改掉 4 处调用（其中 MultiSelect 把按钮当标签用，应改 Badge）→ 删除别名。强调阶梯固定为：`default > outline > ghost > link`。
- 状态全集：`default / hover / active / focus-visible / disabled / loading`，新组件缺一不交付。
- **Button 阴影/hover 规则（v2.6）**：`outline` 用 `shadow-outline`（纯投影，因自带边框）；`outline`/`ghost` 的 hover 底统一用 `fg-5` 中性灰（ghost 旧版用 brand-100 紫底太抢眼，已改）。**实底按钮 hover = 同色加深一档**：`default` → `brand-600`；`destructive` 底色用 `error-strong`（无障碍裁决，见 §0），hover = `error-hover`（在 error-strong 基础上再加深 ≈8% 黑，明暗两端同向加深、无分模式特例，见 v2.6.10）。两个实底变体都必须有 hover，不得只给 default。
- **选中态两种约定（v2.6.7 修订）**：**都不用品牌色**，但按场景分两类——①**导航侧栏 Sidebar**：选中 = `surface` 白底 + `shadow-2`「浮起」，hover = fg-3；②**内容列表 List Item / 菜单项**：选中 = fg-2 灰填充，hover = fg-1。区别理由：侧栏导航项需要"当前页"的强存在感（浮起卡片），内容列表只需轻量区分（灰填充），两者各自内部统一即可，不强行合并。共同底线：选中都不掺品牌紫。
- **标签三分法（v2.6.5）**：徽标按语义分三类，**不可混用颜色体系**——①`状态` 用语义色（success/warning/error/brand），表示运行/连接等动态；②`meta/归属` 用品牌浅底或灰描边（tag-brand/tag-outline/tag-neutral），表示选中/归属/计数等静态分类；③`角色/职位` 用 accent 分类色软底（tag-role-*），纯类目区分，尺寸固定为 `h-4 px-1 text-[10px]`；Lead 角色标签例外使用 `brand-100 / brand-700` 紫色软底。禁止用状态绿表示"已选"、用品牌紫表示"运行中"这类跨类借色。
- **弹窗关闭按钮规则（v2.6.5）**：普通弹窗/可关闭面板右上角放线性 X（`.iclose`，hover fg-1 底）；**alert-dialog（危险确认）不放 X**——高风险操作强制在「取消/确认」间明确选择，多一个 X 是模糊的第三退出路径。
- **Tooltip 规则（v2.6.15）**：提示气泡出现在触发元素下方居中，反色底（`foreground` 底 + `background` 字）、`shadow-3`、圆角 `md(6)`，**不画三角/箭头**。母版不再维护 Avatar 组件，避免与 Chat Message「Agent 无头像」规则冲突。
- DESIGN.md v1 §5 的组件像素规格（Sidebar/Composer/Popover/ToolCard/ContextPanel）仍然有效，但其中色值/字号/圆角一律按本文档 token 替换字面量。
- **加载态三档（v2.7）**：加载指示只有三种形态，由「在哪里等」决定，调用方只选档位和文案，不选尺寸、颜色、位置——
  ①**页面 / 面板首屏** `LoadingState variant="page"`（`PageLoader` 同此）：内容区居中，最小高度 240px，24px spinner `ink-muted`，可选一行 `text-xs ink-meta` 文案在下方；
  ②**区块 / 列表 / 对话框内容重载** `LoadingState variant="section"`：区块内居中，`py-8`，16px spinner `ink-meta`，文案同行；
  ③**行内（按钮 / 表格行 / chip）** `Spinner`：14px，跟随文字色（currentColor），必须与它描述的文字并排，`aria-hidden`（装饰，不改变按钮的可访问名）。
  文案规则：**泛用文案（「加载中…」「Loading…」）一律不显示**，spinner 本身就是信息；只有描述具体操作的文案才作为 label（「正在解析表格」「正在创建第一版工作台…」）。
  Logo 闪光（`PageLoader logo`）只用于应用启动的路由兜底，页面内一律不用。
  禁止：加载态用品牌紫；手写 `Loader2 className="… animate-spin"`；纯文字加载块；页面首屏把 spinner 顶在左上角；同一页两种尺寸。
  例外：刷新按钮上的 `RefreshCw` 在刷新中旋转属于按钮图标状态，不是加载态，不受本条约束。

## 8. 暗色模式

原则：`.dark` 只重定义 **§2.1 基色** + **明确声明的派生锚点**，其余全部自动重算。允许覆盖的锚点仅限两类（已在 tokens.css 注释标明，不得扩大）：
1. **品牌浅阶 brand-50~300**：暗底上的「浅紫」必须是「紫混深背景」，无法用亮色模式的公式推导，故按 `color-mix(brand, background)` 反向重定义。
2. **阴影参数 shadow-rgb / border-a / blur-a**：暗色下阴影需整体加深，调的是这三个强度参数（仍是参数化，非逐值特例）。

除以上两类锚点外，**禁止在 .dark 里写任何非基色覆盖**——若暗色下某处不对，修的是派生配方或上述锚点参数，而不是加一次性特例。

## 9. 迁移对照（codemod 清单）

| 旧值（代码中实测存在） | → 新 token |
|---|---|
| `#6d5cff` `#533afd` `#965cf9`（品牌紫） | `var(--brand)` / `--brand-gradient` |
| `#53cb76`（9 处笔误绿） | `var(--finance-down)` 或 `var(--success)` 按语义 |
| `#9aa3b2` `#898f9c` | `fg-50` |
| `#525860` `#444b54` `#1f2937` | `fg-80` |
| `#f7f7f8` `#F0F1F3` | `fg-1` / `fg-3` |
| `text-[13.5px]` | `text-base`（14px） |
| `text-[12.5px]` | `text-sm`（13px） |
| `text-[11.5px]` `text-[10.5px]` | `text-xs` / `text-2xs` |
| `rounded-[7px]` | `rounded-md` |
| `rounded-[14px]` `rounded-[18px]` | `rounded-2xl` |
| `text-muted-foreground`（63 处） | 保留可用（已 alias 到 fg-60），新代码统一 `text-ink-body` |
| `variant="secondary"`（4 处） | `variant="outline"`（MultiSelect 处改用 Badge） |
| `<Loader2 className="h-N w-N animate-spin …" />`（约 160 处） | 行内 → `<Spinner />`；居中块 → `<LoadingState variant="page \| section" label={…} />` |
| 自绘 CSS 圆环 `animate-spin rounded-full border-2 …` | `<LoadingState variant="section" />` |

## 10. 治理

1. ESLint：tsx 禁 hex 字面量、禁 `text-[npx]` 任意值（CI 红灯）。
2. `scripts/design-audit.sh` 棘轮：违规计数只许减不许增（含 `raw-spinner`：`Loader2`/`LoaderCircle` 手写 `animate-spin`，基线 0）。
3. `frontend/CLAUDE.md` 写入："颜色只用语义 token；新 UI 先查 packages/ui；变体/状态全集见 DESIGN-v2 §7"。
4. CODEOWNERS：`packages/ui/src/styles/**` 由设计负责人 review。
5. 本文档新增模式走文末 changelog：记"新增了什么、从哪个基色派生、为何现有组件不够"。

---

## Changelog

- **2026-09-07 v2.7**：**加载态收敛为三档**（§7 新增条目，§9 补 codemod 行，§10 审计新增 `raw-spinner` 规则）。此前 app/ui/finance/commercial 四个包里约 200 处手写 spinner：尺寸 12–24px 五档、颜色六种（含品牌紫）、有无文案、居中或顶在左上不一。新增 `LoadingState`（`page` / `section` 两档）并让 `PageLoader` 直接渲染 page 档；`Spinner` 收为 14px、currentColor、`aria-hidden` 的行内档（此前 `role="status" aria-label="Loading"` 会混进按钮的可访问名）。全部调用点按档位替换（含纯文字「加载中…」块与页面内的 logo 闪光），泛用文案删除、只保留描述具体操作的 label；`RefreshCw` 刷新中旋转的按钮图标不在此列。
- **2026-07-13 v2.6.18**：修复标签规范四文件漂移——`spec.html` 的 StatusPill / Badge 示例恢复与 `components.html` 母版一致的 `4px` 圆角、完整状态/meta 示例、图标与 warning/error 样式；补齐遗漏的 success/warning/error 三件套 token，移除 queued 多余描边，并同步角色标签固定尺寸说明。`DESIGN.md` / `tokens.css` / `components.html` / `spec.html` 已重新交叉审计。
- **2026-07-03 v2.6.17**：标签规范同步——role / 角色职位标签固定为 `h-4 px-1 text-[10px]`，组件母版新增 `role-tag` 与 Lead 紫色软底（`brand-100 / brand-700`）示例；tokens.css 记录 token 用法，spec.html 补 StatusPill / Badge 三分法示例，DESIGN.md / tokens.css / components.html / spec.html 四文件同步。
- **2026-06-17 v2.6.15**：组件母版从 18 个收敛为 17 个——删除 `17 Avatar（头像）` 整段与相关 `.avatar*` 样式；`Tooltip（提示气泡）` 重编号为 17，改为触发元素**下方居中**，移除三角/箭头伪元素，演示 padding 同步改为下方留白。`components.html` 标题/导语计数与本文档配套文件描述同步。
- **2026-06-17 v2.6.14**：外部全维度复审整改（实算驱动，七处）——①**无障碍·财务色**：新增 `--finance-up-text`/`--finance-down-text`（mix 65% foreground，白底 ≈6.7/5.0:1），数据表与内联涨跌数字（components/spec）改用文字档；基色作 ≤14px 文字白底仅 3.5/2.4:1 未达 AA、此前 §6 漏将 finance 纳入分析（§2.3 + §6 补规则、tokens 补派生与 Tailwind 映射、检测器 TOKENS 收录）。②**文档漂移·阴影**：§4 重写——原文谎称「v2.6.7 合并、不再区分 bordered/borderless」且漏掉在用的 `shadow-1`，与 tokens.css/changelog/母版（`.set-card` 无边框用 shadow-1）三方矛盾；改回明确「有边框 shadow-outline / 无边框 shadow-1」并补列 shadow-1。③**检测器碰撞**：`#f3f4f6`（= fg-2 实测渲染值）仍滞留 DEPRECATED→fg-3，而 fg-2 不在 TOKENS，会把正确的 fg-2 值误判「已废止」（v2.6.13 修了 fg-3 同类碰撞但漏了 fg-2）——fg-2 收入 TOKENS、该键移出 DEPRECATED。④**无障碍·灰底小字**：`pill-queued` 文字 fg-60→fg-80（fg-60 on fg-5 仅 4.05，提到 7.46）。⑤**无障碍·placeholder**：Input placeholder fg-50→fg-60（fg-50 白底 3.4，违反 §6 自定「fg-50 仅 ≥18px/图标」）。⑥**无障碍·amber 角色标签**：tag-role-amber 文字 62%→50%（3.98→5.44，过 AA；amber 最亮需更深，其余色 62% 即达标）。⑦**自洽**：spec.html `.sp-bar` 圆角 3px→4px（3px 为 §4 废止值）；components/audit `h1` 20px→18px（20 不在 8 档字阶）；§0 与 tokens 注释 fg-30 标称由旧近似 #b6b7bc 改注实测 #b1b5ba。
- **2026-06-11 v2**：三方裁决（§0）；token 架构改派生制；字阶/圆角/阴影/图标收敛；新增状态三件套、z-index、动效、无障碍、暗色规则。
- **2026-06-11 v2.1**：error 红 #dc2626 → #e5484d（设计评审反馈：与品牌紫明度不齐，提亮至等亮度）。
- **2026-06-11 v2.2**：灰阶改双轴派生——新增蓝灰极 `slate #444b54`，中段（fg-30~80）沿其派生恢复 v1 冷灰气质；手选色 13 → 14。
- **2026-06-11 v2.3**：Button `secondary` 变体废止并入 `outline`（outline 102 处 vs secondary 4 处，已是事实标准）；强调阶梯固定为 default > outline > ghost > link。
- **2026-06-11 v2.4**：warning #d97706 → #ef8b0c（设计反馈发脏；基色已卸下文字职责）；点缀色 4 → 8（投研图表多系列需要），定固定取色顺序；手选色 14 → 18。
- **2026-06-11 v2.5**：shadow-1 去掉 1px spread 环改纯投影 y1/blur2（设计反馈：环与 fg-8 边框叠加成双描边）。
- **2026-06-15 v2.6**：阴影改分层方案（采纳设计团队反馈，优于 v2.5 的一刀切去环）——新增 `shadow-outline`（纯投影）给有边框元素，`shadow-1` 恢复为投影+`fg-3`浅环给无边框容器；ghost hover 由 brand-100 紫底改 fg-5 灰底。三文件（tokens.css / spec.html / DESIGN）已同步（修复 v2.5→团队版只改 spec.html 未同步 tokens.css 的漏洞）。
- **2026-06-15 v2.6.1**：外部评审修正（均为文档/清理，无视觉数值改动）——①删除 spec.html 残留的死代码 `.btn-secondary`（v2.3 已废止）；②spec.html 头部注明「视觉预览，完整 token 以 tokens.css 为准」；③§6 区分 focus-visible（键盘环）与 Input active focus（柔和内环）两种焦点视觉，明确二者共存；④§8 放宽 dark 表述为「基色 + 明确声明的派生锚点（brand 浅阶 / 阴影参数）」，与 tokens.css 实现对齐。
- **2026-06-15 v2.6.2**：组件库视觉打磨（设计逐项反馈）——表单块去双边框/灰线灰底、菜单扁平化+子菜单箭头、Popover 调淡、Dialog 去演示遮罩、全组件图标统一为线性 SVG（菜单/StatusPill/Toast/Empty）；**新增 `--error-hover` token 并补全 destructive 按钮 hover 态**（此前规范缺失），四文件同步。
- **2026-06-16 v2.6.3**：①destructive hover 减淡（error 混黑 82%→90%，加深幅度对齐 brand-600）；②修复 Settings Section / List Item / toggle 的双描边（border + shadow-1 环），改 `shadow-outline`——再次印证 v2.6 铁律「有边框用 shadow-outline，无边框用 shadow-1」；③列表行「运行中」标签由 pill-done(绿) 统一为 pill-running + 旋转图标，与 Tool Call Card 的 running 一致。
- **2026-06-16 v2.6.4**：统一列表类组件选中态为中性灰（默认/hover/选中 = 透明/fg-1/fg-5），List Item 选中由 brand-50 紫改 fg-5、Sidebar 选中由白底+shadow-2 改 fg-5（设计裁决：选中态不用品牌色，跨组件信号一致）；List Item demo 补「选中/hover/默认」三态标签，消除"为何又紫又灰"的歧义。
- **2026-06-16 v2.6.5**：①Form Section / 普通 Dialog 右上角加线性关闭按钮 `.iclose`，alert-dialog 故意不加（强制明确选择）；②StatusPill/Badge 扩为标签三分法——新增 meta/归属标签（tag-brand/outline/neutral：已派驻/我的/计数）与角色职位标签（tag-role-*：产品经理/设计师/QA 等用 accent 分类色），与状态标签语义分离；③补全 components.html 漏定义的 accent 调色板（导致角色标签 color-mix 回退透明的 bug）。
- **2026-06-16 v2.6.6**：①Sidebar 选中态从 fg-5 灰底改回 surface 白底 + shadow-2「浮起」（设计反馈：导航项需更强存在感），与内容列表的灰填充分为两种约定；②destructive hover 再减淡（error 混黑 10%→7%），与主按钮 hover 幅度持平；③Settings 行内「更改」按钮由 outline 改 default（主按钮）。
- **2026-06-17 v2.6.7**：外部全维度复审修正（多为文档/治理一致性，少量母版违规）——**新增规则**：①§2.3 写入「Toast 是例外，刻意不套三件套」（浮层用中性底 + 基色实心圆图标，避免多条堆叠时整块染色互相干扰），并修掉原三件套注释里「soft 用于 toast 背景」的错误描述。**母版违规修复**（components.html 自己破了规矩）：②Popover 文件徽标 6 个硬编码 Tailwind hex → `.fbadge-md/-csv/-pdf` 用 accent 软底派生（MD=blue / CSV=teal / PDF=orange，PDF 刻意避开 error 红的语义占用）；③Data Table 小数字号 `12.8px → 13px(sm)`（违反「字阶 8 档整数」）；④补齐 components.html 漏定义的 accent-lime/orange/fuchsia（凑满 8 色，杜绝扩展时 color-mix 回退透明，复发自 v2.6.5 同类 bug）。**文档修实**：⑤§7 destructive hover 文案「混 18% 黑/白」→「7%」（与 v2.6.3/v2.6.6 实际值及 tokens.css 对齐）；⑥error `#e5484d` 白字对比度声明由「4.5:1 压线 AA」改实为「≈3.9:1，达 AA Large/UI(3:1)、未达正文 AA」（§0 + tokens.css 注释同步；色值不动，属设计端待决项）。**审计器**：⑦圆角映射 `7px→lg(8)` 修为 `7px→md(6)`（与 §9 一致）；⑧版本标签 v2.4→v2.6；⑨新增 rgb()/rgba() 字面量扫描（转 hex 复用判定，跳过 `rgb(var(--x))` token 写法）；⑩补「覆盖范围」说明（仅扫颜色/字号/圆角字面量，变体名/字重/italic/Tailwind 调色板类走 ESLint）。**spec.html/结构**：⑪§03 状态卡 emoji（✓⚠✕ⓘ）→ 线性 SVG，与组件库一致；⑫Input 高度 34→32、状态类名对齐组件库（.err/.dis）；⑬components.html 按钮 transition 补缓动 `cubic-bezier(.4,0,.2,1)`（对齐 §4）；⑭v1.0 旧稿（16 组件草稿）移出至仓库外 `_archive/`。
- **2026-06-17 v2.6.13**：四次全局复检补漏（均在 `design-audit.html`，前几轮漏掉）——①**真实 bug**：`.snip`（代码片段行）用了 `var(--fg-80)` 但工具 :root 未定义该变量,文字色回退失效;补 `--fg-80:var(--slate)`。②**判定准确性**：`#f0f1f3` 同时是 DEPRECATED 里的 v1 旧字面量与新 fg-3 实测值,会把"已是正确渲染值"误标成"已废止";从 DEPRECATED 移除,改由精确匹配判为"规范值未用 token 名"（修复动作同为换 `var(--fg-3)`，仅标签更准）。复检同时确认:四文件基色/暗色基色零分歧、组件规则无杂散 hex、无"用了未定义"变量、§2.2↔检测器灰阶完全对齐、pill-running 文字对比度浅 7.32/暗 7.78。
- **2026-06-17 v2.6.12**：§7 Button size 由文档声称的 `xs / sm / default / lg / icon`（5 档）收敛为代码与母版实际的 `sm / default / icon`（3 档）——`xs`/`lg` 在 cva 与 components.html 中均未实现,文档不预留未落地档位,消除「设计稿 > 代码」的反向漂移。与 spec.html / components.html §01 的尺寸说明一致。
- **2026-06-17 v2.6.11**：母版图标 stroke 收敛——`components.html` / `spec.html` 此前残留 `1.8 / 2.4 / 2.6` 三个野值（违反 §5「只有 2 与 1.5」）。按渲染尺寸归位:11–16px 的勾/叉/感叹号/spinner/列表图标 `2.4·2.6·1.8`→`2`;20px 空状态图标与 32/40px 头像 `1.8`→`1.5`;24px 小头像 `1.8`→`2`。结果只剩 2 与 1.5 两档,§5 括注同步改为「按渲染尺寸归位」。另:`pill-running` 由「brand-50 底 + fg-12 边 + foreground 字」改为对齐 Info 三件套「brand-50 / brand-200 / brand-700」,与 Info 卡及 §2.3「Info 复用品牌紫」一致（其余 status pill 本就走 X-soft/border/text,running 此前自成一套）。
- **2026-06-17 v2.6.10**：解决 §0 长期挂起的 error 白字对比度待决项——新增派生 token **`--error-strong`**（`color-mix(error 88%, #000)`，即混 12% 黑；白字浅色 ≈5.3:1、暗色 ≈5.1:1，过正文 AA）。`destructive` 实底按钮底色由 `error`→`error-strong`（shadcn `--color-destructive` 同步重指向）；`error-hover` 改为基于 error-strong 再加深 ≈8% 黑，并**删除 .dark 的 error-hover 特例**（两端均引用 `var(--error)` 与黑混合自动重算，回归「暗色零特例」）。error 本体仍用于 soft/border/text 与图标圆底（图标按 UI 3:1 达标，无需压深）。四文件 + 检测器 TOKENS 同步。
- **2026-06-17 v2.6.9**：三次复审机械修正（无视觉数值变更，仅去漂移/补一致性）——①`design-audit.html` 工具自身去硬编码：`.btn-primary:hover` `#5d46e8` → `var(--brand-600)`（补定义 brand-600），`.btn` 高度 34→32 对齐 v2.6.7 控件高度，transition 补 `cubic-bezier(.4,0,.2,1)`；②检测器内置标准色 fg-1/3/5/8/12/30 由近似值改为 `color-mix` 实测渲染值（`#f5f6f8/#f0f1f3/#ebecee/#e3e4e6/#d9d9db/#b1b5ba`），消除 ΔE 边界误判；③§2.2 灰阶表「≈v1 值」列改为「实测渲染值」并同步上述实测值（原列为旧色板近似，与公式产物漂移，尤其 fg-5 `#f5f5f4`→实际 `#ebecee`，易误导）；④`components.html` `.tab` transition 补缓动（v2.6.7 ⑬ 只补了按钮漏了 tab）；⑤`.set-row` 分隔线 `0.5px`→`1px`（亚像素在非 Retina 渲染不可控，且无 0.5px 档）；⑥Toast 示例文案 v2.4→v2.6（版本号过期，正是规范要治的漂移样本）。
- **2026-06-17 v2.6.8**：二次复审，修三处隐性问题（前轮全 token 值已确认四文件零漂移）——①**无障碍**：`pill-queued` 状态文字由 fg-30 改 fg-60（fg-30 在 fg-5 底上 ≈1.5:1，而 queued/待解析 属必读状态，撞 §6「fg-30 不承载必读信息」）；`.lrow .rstate` 同步 fg-30→fg-60；②**同语义同 token**：菜单/Popover 区块标题（`.menu .hd`/`.pop .ph`）由 fg-50@11px 改 fg-60，与 Sidebar `.slabel` 看齐——既满足 §6「fg-50 仅 ≥18px/图标」，又消除"同一 section label 角色用两个 token"的歧义（`.slabel svg` 仍 fg-50，图标合规不动）；③**token 对齐用法**：`--info-soft` 由 brand-100 改 brand-50，对齐 Info 卡 / callout / note 三处实际均用 brand-50 的事实（消除"单一可信源说 100、所有实现用 50"的漂移）。
