# Client Local Backup — 客户端本地数据备份

> **状态：设计稿（未实现）。** 覆盖：数据库（valuz.db + kernel.db，含对话历史）、
> 技能、知识库文档、记忆、附件等本地数据的**版本化本地备份**；用户可设置备份
> 频率、浏览各版本内容、并从任一版本恢复。
>
> 范围限定：本设计针对**默认部署形态**（本地 SQLite + in-process kernel）。
> 显式 `database_url`（共享 Postgres）或远程/沙箱 kernel 的部署不在一期范围，
> 见 §10。

## 0. 拍板结论（先读这个）

1. **备份目录是磁盘上的自描述结构，不依赖应用数据库。** 版本目录 +
   `manifest.json` 即是全部事实；`valuz.db` 里不建版本表。否则恢复时会碰到
   鸡生蛋问题（数据库坏了 → 备份目录还得靠数据库来解读）。
2. **数据库快照必须用 `VACUUM INTO`，不能裸 `shutil.copy`。** 两个引擎全程
   WAL 热连接（host `infra/database.py`、kernel `sqlalchemy_store/engine.py`），
   裸拷贝会拿到主文件 + `-wal` 撕裂的不一致状态。现有的
   `kernel_db_colocate.py` 之所以能 `copy2` 是因为它跑在 boot、引擎打开之前
   ——周期备份没有这个前提。
3. **`secrets/` 默认不备份。** `infra/secret_store.py` 是明文文件（API key、
   OAuth token）。备份目录可能被用户放到外置盘 / 同步盘，把明文凭据复制出去
   是净损害。恢复后需重新登录/填 key，这是明确接受的代价（后续可做口令加密
   的可选项，见 §12）。
4. **版本间用 硬链接/APFS clone 去重，不做块级增量。** 全量快照语义（每个
   版本目录自足、可独立删除）+ 文件级去重（未变化文件零成本），拿到
   Time Machine 式的空间效率而不引入 restic/borg 式的块存储复杂度。
5. **备份内容的"可读性"在备份时物化为 `summary.json`。** "查看某个版本里有
   什么"（多少会话、多少文档、多少技能）在快照时顺手从 DB 数出来写进版本目录,
   UI 直接渲染——绝不在浏览时去打开历史 DB 快照现查。
6. **调度复用仓库既有的模块内 scheduler 惯例**（`docs/scheduler.py`、
   `skills/scheduler.py` 同款：周期 loop + `boot/steps.py` 注册），**不**挂到
   automations 模块——automations 的执行单元是"派一个 agent 会话"，备份是
   系统任务，语义不同。
7. **恢复 = 暂存 + 重启时应用。** 引擎热连接时不可能原地替换 DB 文件；恢复请求
   只把版本暂存到 `restore-pending/`，由 boot 早期步骤（引擎打开之前，紧邻
   `kernel_db_colocate` 的位置）完成替换，替换前自动做一次安全备份。

## 1. 需求与非目标

**需求（来自产品）：**

- 客户端本地数据可备份：数据库、SKILL、对话历史、文档等。
- 用户可主动设置备份频率。
- 用户可查看不同版本的备份内容。

**推导出的隐含需求：**

- 能从某个版本**恢复**（只能看不能恢复的备份没有意义）。
- 备份失败要让用户知道（走既有 notification 账本）。
- 空间可控（保留策略 + 用量展示）。

**非目标（一期不做）：**

- 云端/远端备份目标（COS、S3、WebDAV）——目录结构为此留了余地，但一期只写本地路径。
- 块级增量 / 加密备份。
- KB **源文件**的备份——KB 文档是就地索引（`valuz_document_record.source_path`
  指向盘上任意位置，"indexed in-place, no file copy"），源文件属于用户自己的
  文件体系，不搬运；只备份索引 + 预览 + 资产，并在 manifest 里记录源路径清单
  供恢复时校验（§4）。
- 外部绑定项目目录（`valuz_project.kind="project"` 的 `root_path`）默认不备份
  ——那是用户自己的工作目录（可能是巨大的代码仓库），提供开关。

## 2. 备份范围

数据源以 `FsRegistry`（`infra/fs_registry.py`）为唯一路径口径。按"是否可再生"
分层：

| 类别 | 路径 | 一期默认 | 说明 |
|---|---|---|---|
| **业务库** | `~/.valuz-oss/valuz.db` | ✅ 必备 | agents、projects、KB/技能索引、automations、设置 |
| **kernel 库** | `~/.valuz-oss/kernel.db` | ✅ 必备 | sessions/messages/events = **对话历史**、langgraph checkpoint |
| **KB 派生物** | `docs/`（assets + preview + scan_state） | ✅ | 解析产物，重建成本高 |
| **记忆** | `memories/`（USER.md、MEMORY.md、projects/*） | ✅ | 纯文本，小 |
| **会话附件** | `attachments/<session_id>/` | ✅ | 用户上传原件 |
| **托管 KB 根** | `kb/<kb_id>/` | ✅ | 托管型知识库的内容根 |
| **用户技能库** | `~/.agents/skills/`（`user_skill_root`） | ✅ | 用户自建 SKILL 的本体 |
| **安装标识** | `installation.json` | ✅ | 本地 owner id |
| **托管项目目录** | `~/Valuz/<project_id>`（kind=chat 的 cwd） | ✅（可关） | 会话产出的文件；含 task 运行目录，可能较大 |
| **外部绑定项目** | `valuz_project.root_path`（kind=project） | ❌（可开） | 用户自有目录，尊重边界 |
| **secrets** | `secrets/` | ❌ 硬排除 | 明文凭据，见拍板 3 |
| 可再生/进程级 | `cache/` `models/` `bin/` `logs/` `browser-chrome/` `official-skills/` `memory-review/` `generative-ui/` `skill-creator/staging/` | ❌ | 可重新下载/重建/随包分发 |

范围开关持久化为三个布尔偏好（§6）：托管项目目录、外部绑定项目、用户技能库
（前两个见上表，技能库默认开）。业务库/kernel 库/KB 派生物等"应用数据"不可关。

## 3. 备份目的地与版本目录结构

默认目的地：`~/.valuz-oss-backups/`（用户可改，如指向外置盘）。**必须在
`data_dir` 之外**——避免递归备份，且数据目录整体损坏/误删时备份幸存。

```
~/.valuz-oss-backups/
├── backups.json                  # 轻量目录级索引（版本列表缓存，可丢弃可重建）
├── restore-pending/              # 待恢复暂存区（§8）
└── versions/
    └── 20260716-093000/          # 版本 id = 本地时间戳
        ├── manifest.json         # 本版本事实：文件清单(相对路径+size+mtime+sha256)、
        │                         #   范围开关快照、app 版本、alembic 版本、耗时、总大小
        ├── summary.json          # 业务摘要：会话/项目/agent/技能/文档/自动化 计数，
        │                         #   最近会话标题 TopN —— UI 渲染"这个版本里有什么"
        ├── db/
        │   ├── valuz.db          # VACUUM INTO 产物
        │   └── kernel.db         # VACUUM INTO 产物
        └── data/                 # 文件类数据，镜像原相对路径
            ├── docs/…  memories/…  attachments/…  kb/…
            ├── user-skills/…     # ~/.agents/skills 映射进来
            └── projects/<project_id>/…
```

要点：

- **每个版本自足**：删除任意版本不影响其他版本（硬链接语义保证）。
- `manifest.json` 是**恢复的唯一依据**；`backups.json` 只是列表页的加速缓存，
  损坏/缺失时全量扫 `versions/*/manifest.json` 重建。
- 版本状态机：写入时先写 `manifest.json.partial`，全部完成后原子改名为
  `manifest.json`——没有 manifest 的版本目录视为垃圾，下次运行时清理。
  中断的备份因此天然可辨识。

## 4. 快照机制

单次备份的执行序（`modules/backup/engine.py`，跑在 worker 线程，避免占
event loop）：

1. **预检**：目的地可写；剩余空间 ≥ 上个版本总大小 × 1.2（首个版本用估算的
   源大小）；已有备份在跑则直接拒绝（单飞）。
2. **DB 快照**：对 host DB 经现有 async 引擎执行 `VACUUM INTO '<version>/db/valuz.db'`；
   对 kernel.db 用独立的一次性 aiosqlite 连接执行同样操作。每个库各自事务一致；
   两库之间不追求原子（快照间隔秒级，跨库引用本就以 `event_uid` 等业务键关联，
   可容忍）。`VACUUM INTO` 顺带产出无 WAL、紧凑化的单文件，恢复时直接可用。
   > 注：备份引擎对 kernel.db 是**文件级基础设施操作**，不经 `KernelClient`
   > 业务 seam——这与"host 不得业务查询 kernel 表"的边界不冲突，但要在
   > 代码注释里点明，且仅在 in-process kernel 模式下启用（§10）。
3. **文件快照**：按 §2 范围遍历。对每个文件，与上一版本 manifest 比对
   `(size, mtime)`：
   - 未变 → 对上一版本中的副本做**硬链接**（macOS 上优先 `clonefile`/
     `copyfile(COPYFILE_CLONE)`，失败回退 `os.link`，跨设备回退真拷贝）；
   - 变化/新增 → 拷贝并计算 sha256 写入 manifest。
   跳过 socket/fifo；符号链接**记录为链接**不追踪目标（防外链目录炸范围）。
4. **业务摘要**：从两个 DB 快照（注意：查快照文件，不查在线库，天然一致）数出
   `summary.json`：sessions/messages 计数、项目数、agent 数、技能索引数、KB 与
   文档数、automations 数、KB 源文件清单（`source_path` 列表 + 存在性快照）。
5. **落定**：写 manifest 原子改名 → 更新 `backups.json` → 执行保留策略（§5）
   → 记录 `backup.last_run_*` 偏好 → 失败路径走 notification 账本
   （`kind=backup_failed`，复用 `valuz_notification` 的幂等 ingest）。

**变更检测（避免空转）**：tick 到期后先比对"源指纹"（两个 DB 文件的
`data_version` PRAGMA / mtime + 各范围根目录的聚合 mtime 采样）与上次备份
manifest，若无变化则跳过本轮并顺延 `next_run_at`，不产生新版本。

## 5. 保留策略

默认 GFS 简化版，全部可配：

- 最近 **7** 个版本全保留；
- 之外每天保 1 个、每周保 1 个，最多 **8** 周；
- 总量上限（默认 **20 GB**）超出时从最旧开始淘汰，但**永远保留最近 1 个成功版本**。

淘汰 = 删除版本目录（硬链接引用计数保证共享文件不被误删）。

## 6. 频率设置与调度

**偏好键**（`valuz_app_setting`，走 `modules/settings/preferences.py` 既有
helper 惯例，新增 `KEY_BACKUP_*` + get/set 对）：

| 键 | 取值 | 默认 |
|---|---|---|
| `backup.enabled` | bool | `false`（用户在设置页显式开启） |
| `backup.frequency` | `manual` \| `every_6h` \| `daily` \| `weekly` | `daily` |
| `backup.destination` | 绝对路径 | `~/.valuz-oss-backups` |
| `backup.scope.managed_projects` / `backup.scope.external_projects` / `backup.scope.user_skills` | bool | `true` / `false` / `true` |
| `backup.retention.*` | 数值 | §5 默认 |
| `backup.last_run_at` / `backup.last_run_status` / `backup.next_run_at` | 运行态 | — |

一期用**枚举频率**而非 cron 表达式——设置页一个 Select 讲得清楚；`triggers.py`
的 cron 能力留给以后需要时再接。

**调度器**：`modules/backup/scheduler.py`，照抄 `docs/scheduler.py` 惯例——
周期 tick（60s）读 `backup.enabled` + `next_run_at`，到期把任务投给 engine
（asyncio task + to_thread）；`boot/steps.py` 加
`start_backup_scheduler` / `stop_backup_scheduler`，在 `lifespan.py`
"long-lived runners" 组注册。错过的窗口（应用没开）在下次启动 tick 时立即补跑
一次（`next_run_at <= now` 即触发，与 automations 的 stranded 语义一致）。

## 7. API 契约（先改 `api/openapi.yaml`）

统一前缀 `/v1/backup`，tag `backup`，路由 `api/routes/backup.py` →
`modules/backup/service.py`：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/v1/backup/config` | 频率/目的地/范围/保留 + 运行态（last/next、用量统计） |
| `PUT` | `/v1/backup/config` | 更新配置；改目的地时校验可写、不在 data_dir 内 |
| `POST` | `/v1/backup/runs` | 立即备份，`202` + run 状态；已在跑返回 `409` |
| `GET` | `/v1/backup/runs/current` | 进行中备份的进度（阶段、已处理字节） |
| `GET` | `/v1/backup/versions` | 版本列表（id、时间、总大小、去重后新增大小、状态、summary 摘要） |
| `GET` | `/v1/backup/versions/{id}` | 单版本详情 = manifest 元信息 + 完整 summary |
| `GET` | `/v1/backup/versions/{id}/files?path=` | 按目录层级浏览版本内文件树（自 manifest 渲染，不碰磁盘遍历） |
| `GET` | `/v1/backup/versions/{id}/files/download?path=` | 单文件导出（如取回某个附件/技能文件） |
| `DELETE` | `/v1/backup/versions/{id}` | 手动删除一个版本 |
| `POST` | `/v1/backup/versions/{id}/restore` | 全量恢复：暂存 + 要求重启（§8）；`dry_run` 返回差异预览 |

## 8. 恢复

**全量恢复**（一期唯一恢复模式；单文件导出见上表，已覆盖"捞回个别文件"的
轻量场景）：

1. `POST …/restore` → service 校验版本完整（manifest sha256 抽验 + alembic
   版本不高于当前应用可接受范围）→ 把版本 id 写入
   `<destination>/restore-pending/request.json` → 返回"需要重启应用"。
2. Boot 早期新步骤 `boot/backup_restore.py`（排在两库引擎/迁移之前，与
   `kernel_db_colocate` 同一区段）：发现 pending 请求 →
   **先对现状做一次自动安全备份**（版本打 `pre-restore` 标记）→ 按 manifest
   把 `db/` 与 `data/` 覆写回原位（DB 直接替换文件，WAL/SHM 残留一并清除）→
   写恢复结果 → 清 pending。失败则不动原数据、留错误报告。
   **目录覆写必须是"先物化、后交换"**（payload 完整拷贝到同目录
   `.restore-new` → 两次 rename 交换 → 清理 `.restore-old`），绝不允许
   "先 rmtree 目标再 copytree"：后者在拷贝中途失败时会把目标目录留在
   被摧毁状态（实现期冒烟中 rmtree 半途失败真实毁过一次项目根，靠
   pre-restore 快照救回）。两次 rename 之间崩溃的残局（目标缺失、仅剩
   `.restore-old`）在下次尝试开头自动复位。
3. 备份的 alembic 版本 **低于** 当前应用 → 正常路径，boot 随后的迁移把它升上来；
   **高于**（用户降级了应用）→ 拒绝恢复，提示先升级应用。这与
   `drop_stale` 的既有语义方向一致。
4. `summary.json` 里的 KB 源文件存在性清单在恢复后用于提示："N 个文档的源文件
   已不在原路径，需要重新导入/重新索引"。

## 9. 前端（Settings → 备份）

Registry 驱动接入（`settings-sections.ts` 加 `backup` section +
`SECTION_MAP`/`TAB_ICON_MAP`，新建 `pages/settings/BackupSection.tsx`，全部
文案走 i18n `settings.backup.*`，zh-CN/en-US 同步加键）：

- **开关 + 频率**：`SettingsRow` + Select（手动/每 6 小时/每天/每周）。
- **目的地**：路径展示 + "更改"（Electron 目录选择对话框；WebUI 场景退化为
  文本输入）。
- **范围**：三个开关（托管项目目录/外部绑定项目/用户技能库），secrets 排除
  以说明文案形式固定展示。
- **状态行**：上次备份时间与结果、下次计划时间、总占用/版本数、
  "立即备份"按钮（跑动时显示进度）。
- **版本列表**：时间、大小（总/新增）、摘要徽标（N 会话 · N 文档 · N 技能）。
  点开抽屉：`summary.json` 的业务摘要 + 文件树浏览（懒加载
  `…/files?path=`）+ 单文件导出 + "恢复此版本"（确认对话框，说明会先自动备份
  当前数据并需要重启）+ 删除。
- 失败通知走既有 notification 账本，前端零新增通道。

## 10. 部署形态边界

| 形态 | 行为 |
|---|---|
| 本地 SQLite + in-process kernel（默认） | 全功能 |
| `VALUZ_KERNEL_MODE=http` / 沙箱 kernel | kernel.db 不在本机可控路径：一期**禁用备份**并在设置页说明（后续可给 kernel 加 backup 端点经 seam 走） |
| 显式 `database_url`（Postgres 等） | DB 备份属于 DBA 职责：一期禁用，仅提示 |
| 多用户 headless（`{user_id}` 模板 data_dir） | 一期按单用户实现；范围枚举已按 `FsRegistry(user_id)` 口径写，扩展时逐 user 循环即可 |

## 11. 风险与边界情况

- **备份进行中应用退出** → 无 manifest 的残版本，下次启动清理（§3 状态机）。
- **目的地在外置盘且未挂载** → 预检失败 → notification + 状态行红字，不算崩溃。
- **目的地被用户放进云同步盘** → 允许但在设置页提示明文数据外泄面（secrets
  已排除，但会话内容本身也是敏感数据）。
- **超大托管项目目录**（task 运行产物累积）→ 硬链接去重使增量成本≈0；总量
  上限兜底；进度接口让用户看得到卡在哪。
- **硬链接不可用**（目的地 FAT32/exFAT 外置盘）→ 逐版本真拷贝，功能不损，
  仅空间效率下降；manifest 记录 `dedup: none`。
- **恢复后 secrets 缺失** → 首次启动检测到 provider 凭据不可用时引导重新登录
  （复用订阅渠道 materialize-on-detection 的现状能力）。
- **时钟回拨** → 版本 id 冲突时追加序号；调度以 `next_run_at` 单调推进。

## 12. 后续演进（明确不在一期）

- 口令加密备份（含 secrets 的完整迁移包，用于换机）。
- 云目的地（COS/S3/WebDAV）——目录结构与 manifest 设计已兼容对象存储语义。
- 选择性恢复（单项目/单会话粒度）——需要 DB 级 merge，复杂度高。
- cron 自定义频率（接 `triggers.py` 现成能力）。

## 13. 落点文件清单

| 层 | 文件 | 动作 |
|---|---|---|
| 契约 | `api/openapi.yaml` | 新增 `backup` tag + §7 全部路径 |
| 后端 | `backend/valuz_agent/modules/backup/{engine,scheduler,service,manifest,schemas,errors}.py` | 新增 |
| 后端 | `backend/valuz_agent/api/routes/backup.py` | 新增，挂 `api/app.py` |
| 后端 | `backend/valuz_agent/modules/settings/preferences.py` | 新增 `KEY_BACKUP_*` + helpers |
| 后端 | `backend/valuz_agent/boot/backup_restore.py`、`boot/steps.py`、`boot/lifespan.py` | 恢复步骤 + scheduler 启停注册 |
| 前端 | `frontend/packages/app/src/pages/settings/BackupSection.tsx` | 新增 |
| 前端 | `packages/core/src/edition/registries/settings-sections.ts`、`SettingsPage.tsx` | 注册 section |
| i18n | `i18n/locales/{zh-CN,en-US}.json` | `settings.backup.*` 键 + 重新生成类型 |
| 类型 | `make generate-types` | 契约变更后 |
