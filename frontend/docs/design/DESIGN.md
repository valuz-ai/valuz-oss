# Valuz Design Spec

当前页面的视觉规范整理如下。这份文档的目的不是“描述大概风格”，而是把已经落地在页面里的规则固化成后续页面可复用的标准，避免相同元素在不同页面出现字号、颜色、圆角、图标或间距不一致。

## 1. 使用原则

- 这份文档以当前 `Valuz.html` + `app.jsx` 为准。
- 后续新页面优先复用这里的 token、字号层级、组件尺寸和交互状态，不要重新发明一套近似样式。
- 如果后续确实要调整全局样式，应同时更新页面代码和这份文档，避免文档与实现脱节。
- 相同语义的元素必须复用相同样式。例如：
  `Sidebar` 列表项、弹层菜单项、下拉菜单项、右侧上下文分组、工具卡片状态、输入框、图标按钮。

### 1.1 代码入口

当前规范已经落到代码里，后续新增页面或新组件时，优先从这些入口复用，而不是重新手写一套样式：

- `DESIGN_TOKENS`
- `SHARED_STYLES`
- `panelShellStyle()`
- `floatingShellStyle()`
- `menuShellStyle()`
- `popoverShellStyle()`
- `sidebarRowStyle()`
- `sectionLabelStyle()`
- `panelHeaderStyle()`

这些入口位于 [app.jsx](app.jsx) 顶部常量区，后续同类元素默认先复用这里。

## 2. Foundations

### 2.1 字体

- 主字体：`"PingFang SC"`
- 等宽字体：`monospace`
- 如特殊运行环境覆盖不到上述字体，需在实现前单独确认补充字体策略。
- 默认页面基础字号：`14px`
- 默认抗锯齿：`-webkit-font-smoothing: antialiased`

### 2.2 字号层级

以下字号已经在当前页面里形成稳定层级，后续页面尽量只在这组里选：

| Size | Weight | 用途 |
|---|---:|---|
| `9.5px` | `700` | 文件类型 Badge 文案 |
| `10px` | `600` | Skill 状态标签、极小辅助标识 |
| `10.5px` | `500/600` | Sidebar 分组小标题、Popover Header、文件尺寸、辅助说明 |
| `11px` | `400/600` | Sidebar Section Label、表头、工具状态标签 |
| `11.5px` | `400/500` | 下拉菜单副描述、Section Label、工具详情文本 |
| `12px` | `400/500/600` | Header 辅助信息、按钮标签、meta 信息、内联 code、快捷键 |
| `12.5px` | `400/500` | 下拉选项主文本、文件名、Todo 文本、数据表正文 |
| `13px` | `400/500` | Sidebar Row、Composer 文本、Context 分组标题 |
| `13.5px` | `400/600` | 主消息文本、用户气泡正文 |
| `14px` | `500` | 主内容标题栏标题、页面常规 UI 基准字号 |
| `15px` | `500` | 右侧面板主标题 |

### 2.3 字重

- `400`：正文、Sidebar row、一般说明文字
- `500`：面板标题、分组标题、文件名、表格关键字段
- `600`：Section Label、Popover Header、状态标签、小范围强调
- `700`：极小 badge 文案

### 2.4 颜色 Token

#### 基础背景

| Token / Color | 值 | 用途 |
|---|---|---|
| `--bg` | `#F8F9FB` | 页面大背景 |
| `--surface` | `#FFFFFF` | 主面板、侧栏按钮、弹层底色 |
| `--surface-2` | `#F5F5F4` | 次级浅底、轻量分组背景 |
| `#F7F8FA` | 固定值 | hover 背景、工具卡片底色、上下文分组底色 |
| `#E0E1EA` | 固定值 | 主区域 radial gradient 的浅灰高光 |

#### 边框 / 分割线

| Token / Color | 值 | 用途 |
|---|---|---|
| `--border` | `#E6E7E9` | 常规边框 |
| `--border-strong` | `#DBDBDB` | scrollbar thumb、较强边界 |
| `#F3F4F6` | 固定值 | 分割线、卡片顶部边界、工具卡片内分割 |
| `#D9D9DD` | 固定值 | 工具状态 `running` 的边框 |

#### 文字

| Token / Color | 值 | 用途 |
|---|---|---|
| `TEXT_PRIMARY_COLOR` | `#131313` | 一级文字、选中内容、主要 icon |
| `TEXT_SECONDARY_COLOR` | `#6E7481` | 二级文字、辅助说明、Section Label、普通 icon |
| `TEXT_DISABLED_COLOR` | `#E6E7E9` | 已完成 Todo、禁用态 |
| `--text` | `#131313` | 通用正文 token |
| `--text-2` | `#6E7481` | 通用次级文本 token |
| `--text-3` | `#b6b7bc` | 更弱的辅助信息 |

说明：

- 当前页面里既有 CSS 变量，也有部分组件直接写死 `#131313` / `#6E7481`。后续页面应优先复用统一语义：
  一级文本统一到 `#131313` 或 `var(--text)`；
  二级文本统一到 `#6E7481` 或 `var(--text-2)`；
  更弱辅助统一到 `var(--text-3)`。
- 不要继续引入新的灰阶近似色。

#### 强调 / 状态

| Token / Color | 值 | 用途 |
|---|---|---|
| `--accent` | 默认 `#6D5CFF` | 主行动按钮、品牌强调 |
| `--accent-2` | 默认 `#8B7FFF` | 辅助强调 |
| `--accent-soft` | 默认 `#EDE9FF` | 强调色浅背景 |
| `--accent-sky` / `text-accent-sky` | `#0EA5E9` | 点缀色 1；Context 列表第 1 项 icon |
| `--accent-teal` / `text-accent-teal` | `#14B8A6` | 点缀色 2；Context 列表第 2 项 icon |
| `--accent-amber` / `text-accent-amber` | `#EAB308` | 点缀色 3；Context 列表第 3 项 icon |
| `--accent-pink` / `text-accent-pink` | `#EC4899` | 点缀色 4；Context 列表第 4 项 icon |
| `--context-icon` / `text-context-icon` | `#725CF9` | 右侧 Context 列表 icon |
| `DISCLOSURE_CHEVRON_COLOR` | `#94A3B8` | 所有折叠箭头 |
| `FINANCE_UP_COLOR` | `#F54B4B` | 财务正向值 |
| `FINANCE_DOWN_COLOR` | `#53BC76` | 财务负向值 |
| `--success` | `#16A34A` | 成功状态 |
| `--success-soft` | `#DCFCE7` | 成功浅底 |
| `--warn` | `#D97706` | 警示状态 |
| `--warn-soft` | `#FEF3C7` | 警示浅底 |
| `--danger` | `#DC2626` | 危险状态 |

#### macOS Window Chrome

- 红灯：`#FF5F57`
- 黄灯：`#FEBC2E`
- 绿灯：`#28C840`

### 2.5 圆角体系

| Radius | 用途 |
|---|---|
| `3px` | 极小标签、`kbd` |
| `4px` | 小型状态标签、文件 badge、小 icon action |
| `6px` | 轻量按钮、小行项、SegmentedControl |
| `7px` | Sidebar Row |
| `8px` | 新建对话按钮、Composer 容器、小 icon button、工具卡片、表格卡片 |
| `10px` | Popover、下拉项容器、Composer 输入框内层 |
| `12px` | 主面板、右侧面板、浮层、上下文分组、用户首条气泡、下拉菜单 |
| `14px` | 页面级 token，保留给更大的容器 |

原则：

- 同层级组件尽量共享同一圆角。
- 不要出现 `9px`、`11px`、`13px` 这类新的随机值。

### 2.6 阴影体系

| Shadow | 用途 |
|---|---|
| `0 50px 100px -20px rgba(0, 0, 0, 0.45), 0 30px 60px -30px rgba(0, 0, 0, 0.35), 0 0 0 1px rgba(0,0,0,0.06)` | 应用外壳 |
| `0 24px 48px rgba(28, 25, 23, 0.14)` | 侧栏浮层 |
| `0 18px 40px -18px rgba(17,24,39,0.28), 0 8px 16px -12px rgba(17,24,39,0.18)` | 下拉菜单 |
| `0 12px 32px -8px rgba(0,0,0,0.12), 0 2px 6px rgba(0,0,0,0.04)` | Popover |
| `0 4px 10px rgba(217, 221, 224, 0.8)` | Sidebar active row |
| `0 20px 40px -10px rgba(0,0,0,0.15)` | Tweaks 调试面板 |

### 2.7 间距体系

后续页面优先复用下列间距档位：

| Spacing | 常见用途 |
|---|---|
| `4px` | 微间距、最小 hover 容错、点状 loading 间距 |
| `6px` | Header 内边距、小分组内边距 |
| `8px` | 常规行间距、按钮上下间距、Composer 水平内边距 |
| `9px` | 行项内部 icon + text 的常见 gap |
| `10px` | 卡片内容基础内边距 |
| `12px` | 面板内容内边距、下拉菜单项 |
| `14px` | 较宽的容器内边距 |
| `16px` | 页面级左右 padding |
| `20px` | 主面板 header 左右 padding、标题栏 |
| `24px` | 主消息区左右内边距 |
| `28px` | 主消息区上下内边距 |

## 3. 图标规范

### 3.1 基础规则

- 当前页面使用本地 Lucide 风格 SVG 图标。
- 默认 viewBox：`24 x 24`
- 默认 linecap / linejoin：`round`
- 仅在特殊图标中使用实心填充，例如 `play`、`pause`、`stop`、`checkboxFilled`。

### 3.2 常用尺寸

| Size | 用途 |
|---|---|
| `12px` | 小型说明、表格 title icon、Slash icon、Popover icon |
| `13px` | 小按钮 icon、下拉选中勾、发送箭头 |
| `14px` | Sidebar icon、标题栏文件 icon、Context 分组 icon、新建对话 icon |
| `15px` | 顶部 chrome 上的面板切换 icon、附件 icon |
| `16px` | 收起态侧栏 icon |
| `18px` | Todo checkbox |

### 3.3 描边粗细

| Stroke | 用途 |
|---|---|
| `2` | Sidebar icon、主折叠箭头、主操作 icon |
| `1.9` | Context 分组 icon、附件 icon |
| `1.8` | 模型切换 globe icon |
| `1.7` | 勾选后的 checkbox |
| `1.6` | 项目区加号按钮 |
| `1` | 未完成 checkbox |

### 3.4 图标颜色规则

- 一级 icon：`#131313`
- 二级 icon：`#6E7481`
- 折叠箭头统一：`#94A3B8`
- 不要让同一行里出现 3 种以上 icon 颜色。
- 普通功能 icon 不要随意引入品牌色，除非是主 CTA 或状态提示。

## 4. Layout

### 4.1 应用外壳

- 画布容器：`1440 x 900`
- 页面外层背景：`#5A5866`
- 应用背景：`#F8F9FB`
- 主内容背景附带 radial gradient：
  `radial-gradient(ellipse 62% 74% at calc(25% + 50px) calc(35% + 300px), #E0E1EA 0%, transparent 72%)`

### 4.2 顶部 chrome

- 红绿灯行距顶部：`4px`
- 控制行高度：`28px`
- 左侧起点：`18px`
- 右侧起点：`20px`
- 红绿灯与内容区顶部间距：`4px`

### 4.3 栅格

- Sidebar 宽：`220px`
- Context Panel 宽：`345px`
- 主消息内容最大宽：`760px`
- 主消息内容左右内边距：`24px`
- 当左侧侧栏收起时，主内容不保留固定侧栏占位。

### 4.4 收起态侧栏浮层

- 仅在 hover 顶部菜单开关 icon 时出现，不常驻页面左侧。
- 浮层顶部与内容区顶部对齐。
- 浮层整体外壳：
  `border: 1px solid #E6E7E9`
  `border-radius: 12px`
  `box-shadow: 0 24px 48px rgba(28, 25, 23, 0.14)`
  `backdrop-filter: blur(10px)`
- 浮层左右留白：`8px`
- 浮层内容不改变展开态侧栏内部组件的样式，只调整浮层外壳位置和留白。

## 5. Component Specs

### 5.1 Sidebar

#### 新建对话按钮

- 高度由 `padding: 8px 11px` 决定
- 字号：`13px`
- 圆角：`8px`
- 背景：`var(--surface)`
- 边框：`1px solid var(--border)`
- 底部间距：`8px`
- icon：`message-circle-plus`, `14px`, `stroke 2`, `#131313`
- hover 背景：`rgba(0,0,0,0.03)`

#### Section Header

- 内边距：`6px 8px 4px 10px`
- 字号：`11.5px`
- 字重：`400`
- 字距：`0.06em`
- 颜色：`#6E7481`
- 折叠箭头：`12px`, `stroke 2`, `#94A3B8`

#### Subheader

- 内边距：`8px 10px 4px`
- 字号：`10.5px`
- 字重：`500`
- 颜色：`#6E7481`

#### Sidebar Row

- 内边距：`7px 10px`
- gap：`9px`
- 字号：`13px`
- 字重：`400`
- 圆角：`7px`
- 默认背景：透明
- 选中背景：`var(--surface)`
- 选中阴影：`0 4px 10px rgba(217, 221, 224, 0.8)`
- hover 背景：`rgba(0,0,0,0.03)`
- icon 容器宽：`16px`

#### 收起态图标栏

- 单个图标按钮：`36 x 36`
- 圆角：`8px`
- icon 尺寸：`16px`
- 默认文本色：`var(--text-2)`

### 5.2 Main Panel

- 容器背景：`var(--surface)`
- 边框：`1px solid #E6E7E9`
- 圆角：`12px`
- Header 高度：`48px`
- Header 左右 padding：`20px`
- Header 标题字号：`14px`
- Header 标题字重：`500`
- Header icon：`fileText`, `14px`

### 5.3 Message

#### 用户消息

- 最大宽：`78%`
- 首条消息气泡：`padding 12px 14px`
- 首条消息背景：`#F7F8FA`
- 首条消息圆角：`12px`
- 字号：`13.5px`
- 行高：`1.6`
- 颜色：`#131313`

#### Agent 消息

- 不显示左侧头像
- 字号：`13.5px`
- 行高：`1.7`
- 颜色：`#131313`
- 列表 bullet 颜色：`#6E7481`
- 空行高度：`6px`
- inline code：
  `font-family: var(--mono)`
  `font-size: 12px`
  `padding: 1px 5px`
  `border-radius: 4px`
  `border: 1px solid var(--border)`
  `background: var(--surface-2)`

#### Message Actions

- 按钮尺寸：`26 x 26`
- 圆角：`6px`
- 默认颜色：`var(--text-3)`
- hover 背景：`var(--surface-2)`
- hover 文字色：`var(--text-2)`

#### Thinking Indicator

- 仅保留三颗点，不显示头像
- 点尺寸：`6px`
- 点颜色：`var(--text-3)`
- 容器内边距：`10px 0`

### 5.4 Tool Call Card

- 容器背景：`#F7F8FA`
- 外边框：`1px solid #F3F4F6`
- 圆角：`8px`
- Header 内边距：`9px 12px`
- Tool name：
  `font-family: var(--mono)`
  `font-size: 12px`
  `font-weight: 500`
- Tool label：
  `font-size: 12px`
  `color: #6E7481`
- Tool detail：
  `font-family: var(--mono)`
  `font-size: 11.5px`
  `line-height: 1.6`
  `padding: 10px 12px 12px 32px`

#### Tool Status Tag

- 高度：`17px`
- 内边距：`0 8px`
- 圆角：`4px`
- 字号：`11px`
- `done`：
  文字 `#131313`
  背景 `rgba(83, 188, 118, 0.15)`
  边框 `rgba(83, 188, 118, 0.5)`
- `running`：
  文字 `#131313`
  背景 `rgba(114, 92, 249, 0.08)`
  边框 `#D9D9DD`
- `queued`：
  文字 `var(--text-3)`
  背景 `var(--surface-2)`

### 5.5 Revenue Table

- 外层卡片：
  `border 1px solid #F3F4F6`
  `border-radius 8px`
  `background var(--surface)`
- 标题栏：
  `padding 9px 14px`
  `font-size 11px`
  `font-weight 600`
  `letter-spacing 0.06em`
  `color #6E7481`
  `background #F7F8FA`
- 表格正文：
  `font-size 12.5px`
  单元格 padding：`9px 14px`

### 5.6 Composer

#### 外层区域

- 外层 padding：`10px 20px 16px`
- 内层容器：
  `border 1px solid #E6E7E9`
  `border-radius 10px`
  `padding 12px 8px 8px`

#### 输入框

- 内容区左右间距：`8px`
- 字号：`13px`
- 行高：`1.55`
- 最小高度：`48px`
- placeholder 颜色：`#6E7481`

#### 附件按钮 / icon button

- 尺寸：`28 x 28`
- 圆角：`8px`
- hover 背景：`#F7F8FA`

#### 模型切换按钮

- 高度：`28px`
- padding：`0 8px`
- 圆角：`8px`
- 字号：`12px`
- 打开态 / hover 背景：`#F7F8FA`

#### 发送按钮

- 尺寸：`28 x 28`
- 圆角：`8px`
- 背景：`var(--accent)`
- icon：`heroArrowUp`, `13px`, `stroke 2`
- 文字色：`#FFFFFF`

### 5.7 Popover / Dropdown

#### 通用 Popover

- 背景：`var(--surface)`
- 边框：`1px solid var(--border)`
- 圆角：`10px`
- 阴影：`0 12px 32px -8px rgba(0,0,0,0.12), 0 2px 6px rgba(0,0,0,0.04)`
- Header：
  `padding 8px 12px`
  `font-size 10.5px`
  `font-weight 600`
  `letter-spacing 0.08em`
  `color var(--text-3)`
  `background var(--surface-2)`
- Item：
  `padding 8px 12px`
  `gap 10px`
  hover 背景：`var(--surface-2)`

#### 模型菜单 / 推理菜单

- 背景：`#FFFFFF`
- 边框：`1px solid #E6E7E9`
- 圆角：`12px`
- 阴影：`0 18px 40px -18px rgba(17,24,39,0.28), 0 8px 16px -12px rgba(17,24,39,0.18)`
- 菜单项：
  `padding 10px 12px`
  `border-radius 10px`
  主文案：`12.5px`
  副文案：`11.5px`
  active / hover 背景：`#F7F8FA`

### 5.8 Context Panel

- 容器背景：`#FFFFFF`
- 边框：`1px solid #E6E7E9`
- 圆角：`12px`
- Header 高度：`48px`
- Header 左右 padding：`20px`
- 标题：`15px`, `500`, `#131313`
- 内容区 padding：`8px 8px 0`

#### Context Section

- 外层背景：`#F7F8FA`
- 边框：`1px solid #F3F4F6`
- 圆角：`12px`
- 底部间距：`8px`
- Header：
  `min-height 40px`
  `padding 10px 14px`
  `gap 9px`
- Header 标题：
  `font-size 13px`
  `font-weight 500`
  `#131313`
- Meta：
  `font-size 12px`
  `#6E7481`

#### Todo Row

- 字号：`12.5px`
- 行高：`20px`
- 最小高度：`28px`
- checkbox 尺寸：`18px`
- 已完成文字：`#E6E7E9` + `line-through`

#### File Row

- `padding 6px 4px`
- `gap 9px`
- `border-radius 6px`
- 文件名：`12.5px`
- 文件大小：`10.5px`, `#6E7481`

#### File Badge

- 尺寸：`26 x 20`
- 圆角：`4px`
- 字号：`9.5px`
- 字重：`700`
- 字体：`var(--mono)`

颜色映射：

| Kind | BG | FG |
|---|---|---|
| MD | `#EEF2FF` | `#4338CA` |
| CSV | `#ECFDF5` | `#059669` |
| XLS | `#ECFDF5` | `#047857` |
| IMG | `#FEF3C7` | `#B45309` |
| PDF | `#FEE2E2` | `#B91C1C` |

### 5.9 Tweaks Panel

- 调试面板属于内部工具态，不直接外溢到业务页面
- 容器：
  `width 280px`
  `border 1px solid var(--border)`
  `border-radius 12px`
  `box-shadow 0 20px 40px -10px rgba(0,0,0,0.15)`
  `padding 14px`

## 6. Motion & Interaction

- 常规 hover 背景统一优先使用 `#F7F8FA`
- hover / 背景切换 transition：`.12s`
- 折叠箭头 rotation transition：`.15s`
- 主内容列切换 transition：`grid-template-columns .25s ease`
- Thinking dots 使用轻量 bounce，不扩展到其他业务组件

## 7. Scrollbar

- 默认浏览器 scrollbar 宽：`4px`
- `sidebar-scroll` 默认隐藏 thumb，hover / focus / active 时显示：
  `rgba(137, 143, 156, 0.12)`
- `chat-scroll` thumb：
  `rgba(137, 143, 156, 0.12)`
  `border-radius: 999px`

## 8. Reuse Rules For Future Pages

- 新页面如果出现左侧导航列表，直接复用 Sidebar 的字号、行高、选中态、Section Header 和 icon 规则。
- 新页面如果出现右侧辅助信息面板，优先复用 `ContextPanel` 的 48px header、高度层级、分组卡片背景与边框规则。
- 新页面如果出现对话输入框，复用 Composer 的边框、圆角、8px 水平内边距、28px icon button、accent 发送按钮。
- 新页面如果出现下拉菜单或建议框，优先复用现有 `Popover` / `ComposerModelMenu` 的圆角、阴影、标题和 hover 状态。
- 新页面正文里不要重新定义一套 message text 样式。正文统一沿用：
  `13.5px`
  `line-height 1.7`
  `#131313`
- 新页面如果出现财务、涨跌、同比、环比等数据，继续沿用：
  正值 `#F54B4B`
  负值 `#53BC76`

## 9. 禁止事项

- 不要新增接近 `#131313` / `#6E7481` 的相似灰色。
- 不要在相同层级的面板里混用 `8px`、`9px`、`11px` 这类随机圆角。
- 不要给普通二级按钮上品牌色填充背景。
- 不要让同一类组件在不同页面出现不同字号，仅因为“看起来差不多”。
- 不要把收起态浮层和展开态 Sidebar 写成两套不同视觉体系。浮层只调外壳，不重定义内部组件。

## 10. 后续建议

当前规范已经足够支持后续页面统一，但代码层面仍可以继续收束：

- 把现在散落在 `app.jsx` 里的硬编码颜色继续提取成更稳定的 design token。
- 把 `SidebarRow`、`ContextSection`、`PopoverItem`、`ToolCall` 的样式抽成可复用 helper，减少新页面手写偏差。
- 如果后续页面会继续增加，建议补一份 `component inventory`，把哪些组件允许复用、哪些只能派生，也一起写清楚。
