# Browser 功能 — 设计

> 状态:Implemented(MVP / M0+M1,2026-06-17)。本文是 Valuz 浏览器操作能力的单一设计来源。
>
> 取向一句话:**让任意 runtime 的 agent 驱动一个真实、可见的 Chrome(导航 / 读 DOM / 点击 / 输入 / 截图),引擎直接复用 `chrome-devtools-mcp`(经其 CLI),能力以"渐进披露的 Skill(操作)+ host 实现的 `browser_start`/`browser_stop` MCP 工具(管理)+ Settings 面板(状态/登录)"暴露;浏览器是一个独立、隔离、登录态持久的 Chrome profile。**

---

## 0. 是什么

一个 agent 能自主操作的真实浏览器:

- **三 runtime 通吃**(Claude / Codex / Valuz):操作命令经各 runtime 的 shell 执行,不绑定特定 SDK。
- **桌面 + headless 通用**:能力在 backend,WebUI 也可用。
- **看得见**:连接的是用户屏幕上一个可见的 Chrome 窗口(独立 profile,登一次登录态持久)。

引擎不自造 —— 直接用 Google 官方 [`chrome-devtools-mcp`](https://github.com/ChromeDevTools/chrome-devtools-mcp)(对话框、跨域 iframe、a11y 快照等全由它提供)。Valuz 只写薄薄的"管理 + skill"层。

---

## 1. 架构

```
用户 ──HTTP──> Settings "浏览器" 面板  ┐
              (状态 / 模式 / 打开登录 / 停止)  ├─> host 服务 modules/browser.service ──subprocess──> chrome-devtools start/stop/status
agent ──MCP──> browser_start / browser_stop ┘                                                              │ (daemon, 单 per-user)
agent ──shell(skill)──> chrome-devtools navigate/snapshot/click/… ──attach────────────────────────────────┘
```

三层职责:

| 层 | 形式 | 说明 |
|---|---|---|
| **管理** | `browser_start` / `browser_stop` MCP 工具(模型可调,**实现是 host 代码**)+ Settings 面板(HTTP) | 起/停 daemon,选 profile / 模式 / 旗标。策略 host 拥有,模型只触发。两个前门都打到 `ext.browser_engine`(**§9 引擎端口**):OSS 默认是本进程的 `LocalBrowserEngine`,远程沙箱部署绑自己的引擎 |
| **操作** | bundled **skill**,模型经 shell 跑 `chrome-devtools <tool>` | navigate / take_snapshot / click / fill / type / press_key / take_screenshot / handle_dialog |
| **引擎** | `chrome-devtools-mcp` 的 `chrome-devtools` CLI(daemon 模式) | 连真实 Chrome,跨命令复用状态 |

**为什么这么切**:
- 操作工具有 ~29 个;若都做成常驻 MCP 工具会长期占满每个会话的上下文。改用 **skill 渐进披露**(skill 只一行常驻,用到才读全文 + 跑命令)→ token 最省,且与 Codex 的成熟做法一致。
- 但**管理**(profile 路径、`--headless`、launch-vs-attach、安全旗标)是 host 策略,不能让模型即兴 → 做成 host 实现的 MCP 工具(模型只 `browser_start()` 触发,惰性、会话内、自动化也能自触发),策略仍 host 拥有。
- 二者**共用同一个 `modules/browser.service`**,两个前门(MCP 给模型、HTTP 给 Settings)。

---

## 2. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 引擎 | `chrome-devtools-mcp`(pin `1.2.0`) | 官方、CDP 原生、自带对话框/iframe;catalog 早有此连接器 |
| 暴露方式 | Skill + CLI(操作)/ MCP 工具(管理) | 渐进披露省 token;三 runtime 有 shell → 仍 runtime 中立 |
| 浏览器 | 独立、隔离、可见、持久 profile(`~/.valuz-oss/browser-chrome`)| 不劫持日常 Chrome(单实例限制);隔离 = blast-radius 收口;持久 = 登一次复用 |
| 连接模式 | `managed`(默认,自管 profile)/ `attach`(连用户已起的 Chrome,`--browserUrl`)| managed 覆盖主场景;attach 给 power-user |
| 安全(MVP) | `full_access` + 独立 profile | 平台默认即 full_access;隔离 profile 是实在的边界。完整审批模型见 §6 延后 |

---

## 3. 组件

| 件 | 路径 |
|---|---|
| 引擎端口(daemon **在哪跑**;所有 gate 与前门的唯一入口)| `backend/valuz_agent/ports/browser_engine.py`(`BrowserEnginePort`;OSS 默认 `LocalBrowserEngine`,经 `ext.browser_engine` 绑定)|
| host 服务(本地引擎:detect/status/start/stop,封装 CLI)| `backend/valuz_agent/modules/browser/service.py` |
| DTO / 错误 | `backend/valuz_agent/modules/browser/{schemas,errors}.py` |
| `browser_start`/`browser_stop` 工具(注册进 toolkit `base`/`lead`)| `backend/valuz_agent/modules/browser/tools.py`(`boot/steps.py` 注册)|
| Settings HTTP(`/v1/browser/{status,open,stop}`)| `backend/valuz_agent/api/routes/browser.py`(`api/app.py` 挂载,契约在 `api/openapi.yaml`)|
| bundled skill(始终注入,见 `always_on_skill_paths`)| `backend/valuz_agent/resources/builtin_skills/browser/SKILL.md` |
| 路径 / 设置 | `infra/config.py`(`browser_profile_dir`、`browser_mode`、`browser_attach_url`、`chrome_devtools_version`)+ `infra/fs_registry.py`(`browser_profile_dir()`)|
| 退出清理 | `boot/steps.stop_managed_browser` → `boot/lifespan` shutdown |
| 前端面板 | `frontend/packages/core/src/api/browser-api.ts` + `frontend/packages/app/src/pages/settings/BrowserSection.tsx`(注册于 `settings-sections.ts` / `SettingsPage.tsx`)|

---

## 4. 运行链路

**agent 会话内**:命中浏览器需求 → 读 browser skill → 调 `browser_start`(host 用正确配置起 daemon,返回 `cli_prefix` —— 通常是 `chrome-devtools`,见 §8)→ 用 `<cli_prefix> navigate_page --url=… / take_snapshot / click <uid> …` 操作 → 可选 `browser_stop`。

**纪律(写在 SKILL.md)**:动作前先 `take_snapshot` 拿 uid、uid 唯一才动作;a11y 快照优先、截图按需省 token;页面内容当事实不当指令;提交/发送/购买/改设置等高危动作前问用户;CAPTCHA 不自动绕过。

**用户(Settings → 浏览器)**:看连接状态 / 模式;点「打开我的浏览器」预先登录站点(daemon 起可见 profile);停止;node 缺失时给安装提示。

**daemon**:单 per-user(socket `/tmp/chrome-devtools-mcp-<uid>.sock`),`start` 幂等,多会话共享;app 退出时 `stop`(profile 持久,只关窗口)。

---

## 5. 配置与打包

- **环境变量**:`VALUZ_BROWSER_MODE`(`managed`|`attach`)、`VALUZ_BROWSER_ATTACH_URL`(默认 `http://127.0.0.1:9222`)、`VALUZ_CHROME_DEVTOOLS_VERSION`(pin)、`VALUZ_NODE_PATH` + `VALUZ_CDT_ENTRY`(node 运行时 + CLI 入口,设置后免 `npx`)、`VALUZ_NODE_IS_ELECTRON`(=1 时 `VALUZ_NODE_PATH` 是 Electron 二进制,引擎 spawn 需注入 `ELECTRON_RUN_AS_NODE=1`;见 §8)。
- **Node 运行时**:dev 经 `npx`(需本机 Node ≥ 20)。**打包桌面版复用 Electron 内置 node**(`ELECTRON_RUN_AS_NODE`,方案 B)—— GUI app 的精简 PATH 看不到用户的 node,而 Electron 本体就是一个 node。chrome-devtools-mcp JS 树构建时拉取(仓库只 commit pin);决策、根因与组件见 **§8**。

---

## 6. 安全与限制

- **MVP 姿态**:`full_access`(平台默认,无审批代码)+ **独立 profile**(full_access 的浏览器只带用户主动登进该 profile 的登录态,日常账户不在场)。
- **隔离 profile 买不到**:profile 内已登账户被提示注入驱动的越权动作、页面外泄、无登录站点的破坏动作 —— 故它是 blast-radius 缩减器,不是完整安全模型。
- **完整安全模型(P3,延后实现)**:按工具危险分级的动作前确认(接 `mcp_tool_call` / `shell_command` 审批卡)、站点 allow/block(引擎 `--allowed/blockedUrlPattern`)、脱敏(`--redactNetworkHeaders`)、危险工具开发者开关、不可信内容 system prompt。**触发条件**:功能默认开启 / 面向非开发者 / 进 GA / 涉真实资金或不可逆动作 / 多用户版 —— 任一满足即必须实现。
- **限制**:不支持文件下载;并发会话共享同一 Chrome(约定每会话 `new_page` 隔离;引擎 `--experimentalPageIdRouting` 可强隔离);WSL2 控制 Windows Chrome 需经 MCP 桥(跨 VM 边界直连 CDP 失效)。

---

## 7. 后续工作

- **P3 安全模型实现**(按 §6 触发条件)。
- **P4 可视面板**:把浏览器嵌进 Electron `WebContentsView` 侧栏(desktop-only),attach 同一 CDP 目标。
- **node/CLI vendoring**(出货阻断,desktop)—— 决策 = "复用 Electron 内置 node"(方案 B,2026-07-07 起;此前为 vendor 真实 node),根因 + 计划见 **§8**。
- **Settings 模式切换 UI + 持久化**(当前 `managed`/`attach` 经 env;UI 切换待加)。
- **可部署的 per-agent skill**:当前 browser skill 始终注入;可改为按 agent 部署的目录 skill。

---

## 8. 运行时依赖与 vendoring(desktop)

> 状态:**Implemented**。2026-06-17 落地方案 A(自带真实 node);**2026-07-07 改为方案 B:复用 Electron 内置 node**(`ELECTRON_RUN_AS_NODE`),不再自带独立 node —— 当年否决 B 的"无法 shim"结论经复核有误(见事实 2)。存储策略:**chrome-devtools-mcp 构建时拉取,仓库只 commit pin**(见下)。

**两条事实(第 2 条于 2026-07-07 修正结论):**

1. **打包后的 macOS app 看不到系统 node。** GUI(Finder/Dock)启动的 app 拿到精简的 launchd PATH(`/usr/bin:/bin:/usr/sbin:/sbin`),不含 nvm/Homebrew;`sidecar.ts` 只 `{...process.env}`、直接 `spawn`、不补 PATH。实测:本机 node 在 `~/.nvm/...`,精简 PATH 下 `which node` = NOT FOUND。→ **即使用户装了 node,打包桌面版也不能依赖 PATH 上的 node**(仅终端启动的 dev、或 node 恰在系统 PATH 时可用)。`rg` 不受影响,因为 sidecar 用绝对路径 `VALUZ_RG_PATH`,与 PATH 无关 —— 这正是照搬的范式。

2. **Electron 内置 node 可以复用(方案 B 可行)—— 2026-06-17 spike 的"无法 shim"结论已被实证推翻(2026-07-07)。** 当年的**观察**正确:`ELECTRON_RUN_AS_NODE=1` 下 `process.versions.electron` 仍有值且 `process.defaultApp=undefined`,yargs `hideBin` 误判为"打包 Electron app"走 `argv.slice(1)` 而非 `slice(2)`,脚本路径漏进 positional → 子命令解析全错(已复现:`status` 打出 usage 而非执行)。但**结论**错了:"`start` 经 `process.execPath` 再 spawn daemon、无法 shim"只对*入口包装式* shim 成立 —— daemon 的 spawn 是 `env: {...process.env, …}`(`build/src/daemon/client.js`),**环境变量完整继承**,所以凡随 env 传播的修正(env 预载,或直接 patch 掉 hideBin 的误判)自动覆盖 daemon 及其后代(watchdog / update-check 同为 `execPath` spawn + env 继承)。**已端到端验证**(Electron 36.9.5 + chrome-devtools-mcp@1.2.0):shim 下 `start → status → stop` 全链路正确,daemon 进程即 Electron 二进制、其内部 yargs 参数解析全对。当年 spike 只试了入口包装(确实覆盖不到 daemon),未试 env 级注入。

**方案 B(现行):Electron 二进制当 node 用(`ELECTRON_RUN_AS_NODE=1`)+ 构建期 patch,`<electron> <entry>` 绝对路径调用(仍沿用 `rg` 的绝对路径范式)。**

- **shim 形态选"构建期 patch"而非 NODE_OPTIONS 预载**:Phase A4 `npm ci` 之后、staging 之前,`scripts/patch-cdt-electron-node.cjs` 给 bundled third_party 的 `isBundledElectronApp()` 补上 `&& !process.env.ELECTRON_RUN_AS_NODE`。单点定义、所有进程(CLI/daemon/watchdog)共用;版本 pin,patch 稳定,预期文本缺失即 build fail-loud;且不依赖 `EnableNodeOptionsEnvironmentVariable` fuse。
- **代价/前提(采纳时已知)**:要求打包 app 的 **`RunAsNode` fuse 保持开启**(Electron 默认开;本仓库未动 fuses)。Electron 加固指南建议生产应用关闭该 fuse(签名二进制可被本地攻击者当通用 node 解释器滥用)。**若未来安全加固需要关 fuse,回退方案 A**(vendor 真实 node;`scripts/download-node.sh` 在 git 历史中)。
- 收益:每平台包体 −~100MB 未压缩(DMG −~30MB);CI 少一个平台相关下载步骤;air-gap 顾虑减半(只剩 npm ci)。

存储策略(**构建时拉取,仓库只 commit pin**):

| 件 | 体量 | 存储 | 理由 |
|---|---|---|---|
| chrome-devtools-mcp JS 树 | ~17MB,**平台无关**(依赖 `puppeteer-core`/`ws`/`yargs` 已打进 `build/`,一份通吃)| 只 commit `package.json` + `package-lock.json`;`node_modules` 构建时 `npm ci` 生成,**不进 git** | 整棵 ~350 文件的第三方树进 git 无收益;完整性靠 lockfile 的 integrity SHA(`npm ci` 校验)|
| node 运行时 | 0(复用 Electron 本体)| — | 方案 B:`ELECTRON_RUN_AS_NODE`;独立 node 二进制及 `backend/vendor/node/`、`scripts/download-node.sh` 已随方案 A 退役 |

代价:打包需联网(release CI 有网);本特性不支持完全 air-gapped 的桌面构建。

已落地组件:
1. **JS pin**:`backend/vendor/chrome-devtools-mcp/{package.json,package-lock.json}`(commit;`node_modules` gitignored;刷新/bump 用 `scripts/vendor-chrome-devtools-mcp.sh`)。
2. **Electron-as-node patch**:`scripts/patch-cdt-electron-node.cjs` —— 见上;由 Phase A4 在 `npm ci` 后调用,幂等,预期文本变化(upstream bump)时 fail-loud 提醒同步。
3. **`build-desktop.sh` Phase A4**:`npm ci --omit=dev` 装 chrome-devtools-mcp → patch → stage 进 `libexec/chrome-devtools-mcp/`(`--skip-node` 跳过整个 phase;顺带清理旧版本遗留的 `libexec/node`)。
4. **`sidecar.ts`**:staged CDT 树存在时,设 `VALUZ_NODE_PATH=process.execPath`(即 Electron 本体)+ `VALUZ_NODE_IS_ELECTRON=1` + `VALUZ_CDT_ENTRY`(`libexec/chrome-devtools-mcp/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools.js`)为绝对路径,绕开 GUI app 的精简 PATH。
5. **`modules/browser/service.py`**:`_engine_argv()` 是*真实*调用 —— 两个 env 都设 → `[node, entry]`;否则 `npx`(仅 dev/带系统 node 的 headless)。host 自己的 status/start/stop 直接用它。`node_available()`:两个 env 都设即可用,否则探测系统 node。**`VALUZ_NODE_IS_ELECTRON=1` 时,引擎 spawn env 注入 `ELECTRON_RUN_AS_NODE=1`(`_engine_env()`)—— 只注入引擎相关 spawn,绝不进全局 `os.environ`**(否则会漏进 claude/codex CLI 等其他子进程;env var 本身只对 Electron 二进制生效,但作用域仍收紧到位)。缺了它 Electron 会以 GUI 模式启动(弹第二个 Valuz 实例)而不是当 node 用。
6. **友好命令 `chrome-devtools`(显示友好)**:绝对路径前缀会原样显示在客户端工具卡里、很丑。故 `ensure_cli_on_path()` 在 **boot** 时(早于任何 session spawn —— env 在 spawn 时被继承,不是实时)往 `FsRegistry.browser_bin_dir()`(`~/.valuz-oss/bin`)写一个 wrapper(posix `sh` / win `.cmd`,内容 `exec <engine argv> "$@"`;Electron-as-node 模式下 wrapper 内嵌 `export ELECTRON_RUN_AS_NODE=1` / win `set`,daemon 再 spawn 靠 env 继承自动带上),并把该目录 **prepend 进 `os.environ["PATH"]`**;三 runtime 的 agent shell 都继承此 PATH —— Claude SDK 继承父 env、Codex `dict(os.environ)`、DeepAgents `LocalShellBackend(inherit_env=True)`(**其默认是空 env、无 PATH,必须显式开启**,否则连 wrapper/npx/node 都解析不到 → exit 127)。于是 `cli_prefix()` 返回 `chrome-devtools`,agent 跑/显示的就是 `chrome-devtools take_snapshot …`;wrapper 装不上时回退到真实前缀。dev 与打包一致(dev 底层仍 npx)。
7. **gate**:引擎不可用时(本地引擎 = env 未全设且系统无 node),`capability_resolver.always_on_skill_paths` 不注入 browser skill、`boot/steps` 不注册 `browser_start`/`browser_stop`(也不装 wrapper),避免 headless/TUI 广告一个跑不起来的功能。三处 gate 都读 `ext.browser_engine.available()`(§9),不再直接读 `node_available()`。

**平台矩阵**:node 运行时 = 各平台自己的 Electron 本体(mac arm64/x64、linux arm64、win x64 天然全覆盖,无需 dist tag → node target 映射);JS 树一份共享。

**范围与边界**:本节 = **本地引擎**(desktop)。headless/TUI 的本地引擎暂不支持(将来支持时:无 Electron 可复用 → 届时重新引入独立 node —— 恢复 `scripts/download-node.sh`(git 历史)或把 node+包打进 valuz-server 的 PyInstaller bundle + `sys._MEIPASS` 自定位,见 `_detect_rg` 的 frozen 分支)。**Chrome 仍由用户自带**(puppeteer-core 按安装位置查找,不受 PATH 影响)。agent shell 不在 host 上跑的部署(云沙箱)走 **§9 的远程引擎**,与本节无关。

---

## 9. 引擎端口:daemon 在哪跑(`BrowserEnginePort`)

> 状态:Implemented。动机来自云沙箱部署:agent 的 shell 在一台**别的机器**(每 owner / 每 session 一个沙箱 microVM)上跑,而 §1–§8 的管理层全部假设 daemon 与 shell 同机(CLI 经本地 unix socket 找 daemon)。host 上就算装齐 node + Chrome 也没用——daemon 起在 host,沙箱里的 `chrome-devtools navigate_page` 连不到。

**契约**(`backend/valuz_agent/ports/browser_engine.py`,经 `ext.browser_engine` 绑定):

| 方法 | 谁调 | 语义 |
|---|---|---|
| `available()` | boot(注册工具)+ `always_on_skill_paths`(注入 skill)| 本部署能否跑 daemon。本地引擎 = 探测 node;远程引擎 = **对镜像的声明**(同 `RuntimeAvailabilityPort` 的思路) |
| `bootstrap()` | boot,仅当 available | 一次性准备;本地引擎装 `chrome-devtools` wrapper 进 PATH。永不抛 |
| `status / start / stop(user_id, session_id=None)` | `browser_start`/`browser_stop` 工具(带 session)、Settings 路由(只带 owner)| 在**该 session 的 shell 所在处**起/停/看 daemon。scoped allocator 下每 session / task 一个沙箱,所以远程引擎需要 session 定位;`kernel_client.sandbox_scope_for(user_id, session_id)` 给出同一个 scope |
| `shutdown()` | host 进程退出 | 本地引擎关 Chrome;远程引擎通常 no-op(沙箱自己有生命周期)|

**OSS 默认 `LocalBrowserEngine`** = §3–§8 的全部行为原样(桌面零变化)。DTO(`BrowserStatus` / `BrowserStartResult`)与错误(`BrowserError` 族)从端口模块再导出,overlay 只依赖端口。

**`mode="sandbox"`**:远程引擎在 `BrowserStatus.mode` 报 `sandbox`,Settings 面板据此隐藏「打开我的浏览器」(无窗口可开)、改显示说明文案;`cli_prefix` 仍是 `chrome-devtools`(镜像把 wrapper 放进 PATH)。

**远程引擎要自己解决的事**(OSS 不替它决定,但实测过、写下来省一次踩坑;2026-09 云沙箱 spike):

- **策略旗标烘进镜像**,不靠 CLI 转发:chrome-devtools-mcp 1.2.0 把 `--chromeArg=--no-sandbox` 拆成两个 token 重新 spawn daemon,yargs 把 `--no-sandbox` 当布尔开关吃掉,root 下 Chrome 秒退(症状 `Protocol error (Target.setDiscoverTargets): Target closed`)。正解是 `--executablePath` 指向一个注入 `--no-sandbox --disable-dev-shm-usage --disable-gpu …` 的包装脚本——这也正是 host-owned 策略(§1)在镜像里的落点。
- **无头 + 无显示**:「打开浏览器登录」这一半没有对应物;L1 只做 agent 自主浏览,登录态、可视面板另立方案。
- **导航超时**:默认 3 s,重页面必超;skill 已教模型 `--timeout=30000`。导航到不响应的地址后 daemon 会整个卡死(后续命令一律 60 s `Timeout waiting for daemon response`),skill 已教 `browser_stop` → `browser_start` 恢复。
- **多用户即触发 §6 的 P3 条件**:至少带 `--blockedUrlPattern`(私网段 / link-local)与 `--redactNetworkHeaders`。
- 云端出口 IP + `HeadlessChrome` UA 会招反爬验证码;按 skill 纪律不绕过。
