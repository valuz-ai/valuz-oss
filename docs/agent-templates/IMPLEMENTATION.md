# Agent Templates 模板库 — 落地实施计划

> 把 [PRD.md](PRD.md) + [teams.md](teams.md) 设计的「场景团队模板库」落地。
> 本期范围（已确认）：**只新增 3 套新 team**（投研 / 小红书 / 世界杯），onboarding 现有 team 不动；
> **连装备一起做**（skill 文件 + MCP 连接器）；文案**严格 i18n 双语**。

## 0. 关键架构结论（勘察已确认）

| 维度 | 结论 | 依据 |
|---|---|---|
| Agent 字段 | `AgentRow` 已有 slug/avatar/skills/connector_types/effort/source，**不改表** | `modules/agents/models.py` |
| Skill 落地 | 做成 **bundled official skill**：提交进 `resources/official_skills/`，boot 时 `sync_bundled_official_skills()` 自动同步到 `~/.valuz-oss/official-skills/`，带 `.bundled-version` 标记 → 免费、开箱、**不被 entitlement gate** | `integrations/skills_official_bootstrap.py`、`capability_resolver.py:172` |
| MCP 落地 | 进 `resources/connector_catalog.json`；stdio 类型由 `mcp_resolver._build_stdio_config()` 拉起 | `adapters/mcp_resolver.py` |
| 模板定义 | 新建 `modules/agent_templates/` 模块；文案走 i18n（`agentTemplates.*`），结构字段（slug/avatar/skills/connectors/effort/runtime）在模块内 | 参照 `onboarding.py` |
| 契约优先 | 先改 `api/openapi.yaml` → 后端 → `make generate-types` → 前端 | 项目铁律 |

### 源素材
- 投研 13 个 skill：源自 [claude-for-financial-services-cn](https://github.com/jwangkun/claude-for-financial-services-cn)，移植后**去掉 `china-` 前缀、改写为全球股票市场口径（美股/港股/A 股为主）**，落在 `resources/template_skills/`（每个就是一个 `SKILL.md`）。
- 数据工具：投研团队统一用 Valuz 自有 MCP——`valuz-search`（财报/电话会/研报/纪要/公告检索）+ `valuz-data`（行情/财务/指标），均为 HTTP/OAuth，已在 `connector_catalog.json`。（早期曾计划移植 wind/ifind/akshare/china-news 四个第三方 stdio MCP，已弃用。）
- 小红书 4 + 世界杯 3 个 skill：**自研**，方法论见 teams.md 各角色指令 + harness-100 参考。

### MCP 运行时（已定）
投研团队改用 Valuz 自有 MCP（`valuz-search` / `valuz-data`，HTTP transport + OAuth），无需移植第三方 stdio MCP、不引入 akshare/pandas 依赖，登录后开箱可用。小红书/世界杯仍用各自的 stdio/HTTP connector。

## 阶段与任务

### Phase 1 · 装备层 - Skills（无依赖，可先做）
1. 移植 13 个投研 skill → `resources/template_skills/`，**去掉 `china-` 前缀并改写为全球股票市场口径**。
2. 自研 7 个 skill（`xhs-topic-method`/`xhs-note-writing`/`xhs-visual-method`/`xhs-publish-playbook` + `wc-scouting`/`wc-forecast-synthesis`/`wc-poster-design`）→ `resources/official_skills/`，按 teams.md 提纲撰写 `SKILL.md`。
3. 给所有新 skill 打 `.bundled-version` 标记（或确认 boot sync 自动打）。验证 boot 后 skill library 可见、未连 Reportify 也可用。

### Phase 2 · 装备层 - MCP 连接器
4. 定 stdio 运行方案（A/B/C），落 MCP server 代码位置。
5. `connector_catalog.json` 备齐 connector：投研用 `valuz-search`/`valuz-data`（已内置，HTTP/OAuth）；小红书/世界杯用 `xhs-search-mcp`/`web-search-mcp`（双语 display_name/description）。
6. 验证 `valuz-search`/`valuz-data` 能被 mcp_resolver 解析、agent session 能调用。

### Phase 3 · 后端模板库 feature（契约优先）
7. `api/openapi.yaml`：`AgentTemplate`/`TemplateRole`/`AddTemplateResponse` schema + `GET /v1/agent-templates` + `POST /v1/agent-templates/{id}:add`。
8. 新建 `modules/agent_templates/`：模板定义（3 套 team，引用 Phase 1/2 的 slug/connector）+ service（`list_templates` / `add_template` 固定 slug 幂等去重，复用 `_resolve_deploy_target`+`get_default_effort`）。
9. `api/routes/agent_templates.py` 路由 + `app.py` 注册。
10. 处理 id 冲突：投研 team 用新 id（如 `investment-pro`）避开 onboarding 现有 `investment`。

### Phase 4 · i18n 双语
11. `i18n/locales/{zh-CN,en-US}.json` 增 `agentTemplates.*`（3 team × 4 role 的 name/description/instructions 全量双语）+ 模板面板 UI 文案。
12. `cd backend && uv run python ../i18n/scripts/gen_types.py` 重新生成 key 类型。

### Phase 5 · 前端模板面板
13. `make generate-types` 后，`packages/core/src/api/agents-api.ts` 增 `AgentTemplate` 类型 + `listTemplates`/`addTemplate`。
14. `packages/app/src/components/agent-icons.ts` 补 6 个 icon（gem/trophy/calculator/activity/presentation/image）。
15. `packages/app/src/components/AgentTemplatesPanel.tsx`（Dialog，按场景分组，team 卡 + 「添加到我的库」批量创建 + 已添加禁用）。
16. `packages/app/src/pages/AgentsPage.tsx` 顶部加「浏览模板」入口 + 添加后刷新列表。

### Phase 6 · 验证
17. `make test-all` / `make typecheck` / `make lint` 全过。
18. 浏览器走查：浏览模板 → 添加投研 team → 库里出现 4 个 agent → 派进项目 → 起会话确认 skill/MCP 挂载。

## 风险与边界
- **MCP 接入**：投研用 Valuz 自有 HTTP MCP（`valuz-search`/`valuz-data`），OAuth 登录即用，无第三方 stdio 运行时风险。
- 世界杯 team 是 2026 限时（6.11–7.19），当前在窗口内，值得做但生命周期短。
- 自研 7 个 skill 的质量取决于 teams.md 提纲的可操作性，撰写后需自查。
