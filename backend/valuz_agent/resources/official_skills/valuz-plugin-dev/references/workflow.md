# 命令、确认卡片、调试、发布

## 命令速查

| 命令 | 作用 |
|---|---|
| `valuz-plugin create <目录> --id <发布者.名字>` | 在工作区生成插件脚手架（清单、`src/`、`locales/`、测试、`AGENTS.md`）。生成后先 `ls` 看内容 |
| `valuz-plugin build` | 构建前端到清单的 `frontend.entry`（ESM；`react`、`react-dom`、`@valuz/plugin-sdk` 改写为取宿主共享模块；自带 CSS 加作用域；拷贝清单 / 文案 / 自动化脚本；检查产物只 import 了共享模块） |
| `valuz-plugin test` | 在测试宿主里加载插件，检查注册内容、是否越过公开扩展面，并跑你写的测试 |
| `valuz-plugin validate` | 校验清单与包结构（与安装时、控制面、`extension_manager validate` 同一套规则） |
| `valuz-plugin pack` | 打成 zip（不含符号链接 / `..` / 绝对路径） |

命令都在插件目录里执行（或按 `--help` 指定）。`valuz-plugin` 缺失时不要自己造替代品，告诉用户。

## `extension_manager` 动作与确认

| 动作 | 要用户确认？ | 说明 |
|---|---|---|
| `status {id?}` | 否 | 状态、原因、来源、权限、`revision`、开发目录 |
| `validate {path}` | 否 | 与 `valuz-plugin validate` 同规则，返回 `valid`、`errors`、`warnings`、`manifest` |
| `pack {path, out_dir?}` | 否 | 返回 zip 路径、`sha256`、`size` |
| `dev_link {path}` | **是** | 把工作区内的目录以开发模式装进本机 Valuz；只确认一次，之后每次重新构建自动重载 |
| `install {source_path \| url}` | **是** | 装一个固定的 zip / 目录；同 id 版本更高即更新 |
| `uninstall {id, purge_data?}` | **是**（危险） | 卸载；`purge_data` 同时删存储数据 |
| `reload {id}` | 否 | 开发链接的插件重载（`revision`+1） |
| `logs {id, limit?}` | 否 | 最近的日志行：`ts` `level` `message` `source`（`frontend` = 插件的 `ctx.log` 与加载错误、`backend` = 宿主、`audit` = 被拒绝的请求和写操作） |
| `publish {path \| id, scope, distribution_ids?, notes?}` | **是** | 打包并上传到个人 / 组织 / 全局目录；需 Valuz 账号 |
| `submissions` | 否 | 我的提交与审核状态 |

要确认的动作返回 `{ ok: true, action, message, operation }`：`operation` 就是聊天里的确认卡片。拿到它之后：

1. 用一句话告诉用户你提议了什么、卡片在哪；
2. **停下**。不要再调同一个动作，不要假定已经装好，不要换个方式（比如 shell 里 `valuz plugin install`）绕过；
3. 用户确认后再 `status` / `logs`；用户取消 / 卡片过期就问他要不要重新提议。

卡片显示的内容：插件 id、版本、发布者、来源（路径 / URL）、sha256、权限（更新时新增的权限单独标出）、未满足的 `requires`、是否带后端；发布时还有范围与目标发行版。**确认的是卡片上的这份内容**：之后再改 zip 或目录（`install` / `publish`），确认会因内容变了而失效，需要重新提议。

限制：

- `dev_link` 只接受当前会话工作区内的目录（符号链接会被解析后判断）。目录在工作区外就把插件建在工作区里。
- `dev_link` / `install` / `uninstall` / `reload` 只在本地会话可用；云端会话里这些动作返回 `local_only`，你可以继续写代码、`validate`、`pack`、`publish`，装的事交给用户在桌面端做。
- 没有 Valuz 账号 / 没有插件目录时 `publish`、`submissions` 返回 `publish_unavailable`（"需要登录 Valuz 账号"）。

## 调试顺序

1. `status {id}`：先看 `status` 与 `status_reason`。
2. `logs {id}`：`source: frontend` 的 `error` 行是加载 / 运行错误；`audit` 行里的拒绝说明权限没声明。
3. 对症：见 SKILL.md「常见失败」。
4. 修好后 `valuz-plugin build` →（需要时）`reload` → 让用户再看；**界面效果以用户的反馈为准**。
5. 在 `apply` 和关键流程里多用 `ctx.log.info/warn/error`，写到的就是 `logs` 读到的那份日志。

## 发布与分享

1. 版本号递增（SemVer）；`valuz-plugin build && test && validate` 全过。
2. `valuz-plugin pack`（或 `extension_manager pack`）得到 zip。
3. `extension_manager publish {path: zip 或目录, scope: "personal" | "org" | "global", distribution_ids?, notes?}`：
   - `personal`：只有用户本人可见可装，只做自动检查；
   - `org`：本组织成员可见，需组织审核人通过；
   - `global`：进指定发行版的市场，需平台审核；第一次全局发布前用户要先填开发者资料；
   - 同一个版本号上架后不可变；改了代码必须升版本再提交。
4. 用户在卡片上确认后由 Valuz 用当前登录态上传；提交记录里会附"由 Valuz 会话生成"和会话 id。
5. `extension_manager submissions` 看自动检查 / 审核结果（被拒时有原因）；按原因修改、升版本、重新提交。

## 给用户的话术要点

- 提议安装 / 链接 / 发布后：说清"我提议了 XX，卡片里请核对权限和来源，点确认后我再检查加载结果"。
- 需要权限时说清用途："要读取项目文件，所以声明了 `projects:read`"。
- 结束时汇报：做了什么、装在哪（开发链接 / 正式安装）、怎么打开（页面路径或插槽位置）、还有什么没验证（例如界面效果等用户确认）。
