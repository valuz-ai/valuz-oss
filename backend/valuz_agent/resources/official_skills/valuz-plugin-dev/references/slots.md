# 12 个公开插槽

用 `ctx.registry.slot(name, { id, component, key?, label?, priority? })` 往下面的插槽贡献组件。`list` = 多个贡献依次渲染；`keyed` = 每个贡献一个标签页，必须给 `key`（和 `label`）。
组件拿到的 props 就是表里列的（`ComponentType<SlotProps<"槽名">>`）。**只有这 12 个**（加页面 / 设置分区 / 侧栏项另有 helper），别的插槽名会让插件加载失败。

| 插槽 | 种类 | 宿主传的 props | 典型用途 |
|---|---|---|---|
| `conversation.title.actions` | list | `sessionId`、`session`、`scrollToTop()`、`turns` | 会话标题栏的按钮（导出、合规检查） |
| `conversation.header.badges` | list | `sessionId`、`session`、`project`、`agentSlug` | 会话头部的状态徽标 |
| `conversation.turn.actions` | list | `turn`、`scrollToTurn()` | 每轮回复下面的操作 |
| `conversation.tool-card.<工具名>` | list | `tool`、`toolUseId`、`status`、`input`、`output`、`thinking`、`hostRef` | 替换某个工具的结果卡片（数据商的行情卡片）；`<工具名>` 是裸工具名 |
| `conversation.composer.plus.menu-items` | list | `sessionId`、`projectId`、`setDraft(text)`、`surface` | 输入框「+」菜单：插入数据、模板 |
| `conversation.empty.extra` | list | `projectId`、`agentSlug`、`setDraft(text)` | 新对话空白页的快捷入口 |
| `context-panel.tabs` | keyed | `projectId`、`sessionId`、`surface` | 右侧上下文面板的标签页 |
| `project.detail.tabs` | keyed | `projectId`、`navigate(path)` | 项目页的标签页（客户看板） |
| `project.detail.header.actions` | list | `projectId`、`project`、`navigate(path)` | 项目页头部的按钮 |
| `resource.<类型>.actions` | list | `resourceType`、`resource` | 技能、智能体、连接器等资源上的操作；`<类型>` 如 `skill`、`agent`、`connector` |
| `shell.topbar.actions` | list | `pathname`、`activeProjectId`、`rightPanelCollapsed` | 顶栏按钮 |
| `task.detail.sections` | list | `task`、`isCompleted` | 任务详情里的区块 |

补充：

- `ToolCardSlotProps` 里 `status` 是工具调用状态字符串，`input` / `output` 是工具的原始入参 / 结果（`unknown`，自己校验结构），`thinking` 是思考内容。工具卡片槽位对第一方工具和 MCP 连接器的工具都有效（工具名以会话里显示的为准）。
- `setDraft(text)` 把文字填进输入框，不发送。
- `navigate(path)` 与 `host.navigate` 一样，可以跳到自己的页面 `/x/<插件 id>/…`。
- **不公开**（要么牵涉安全，要么布局常变，要么会改掉第一方渲染）：外壳品牌、侧栏内部结构、输入框的布局类插槽、审批卡、新手引导、设置页头、整个替换第一方工具卡片 / 消息气泡的包裹点。需要这些时告诉用户做不到，别硬找替代的内部入口。
- keyed 插槽示例：

```tsx
ctx.registry.slot("project.detail.tabs", {
  id: "dashboard",
  key: "dashboard",
  label: "tab.title",          // locales 的键，或纯文本
  component: DashboardTab,     // (props: { projectId: string; navigate: (path: string) => void }) => JSX
});
```
