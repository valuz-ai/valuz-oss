# 任务完成后继续对话(收尾微调)实现计划

> **HISTORICAL (executed 2026-06; APIs renamed since).** 本文引用的
> `register_dispatch_tools()` 闭包机制已在 2026-07 重构中退役——现为
> `tools/handlers.build_task_tool_defs`(模块级 handler + 声明表 zip),
> orchestrator 方法经 `task_orchestrator.<service>.<method>` 访问。
> 本文仅作实施记录保留,不再反映当前代码。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `completed` 任务的详情页内嵌一段与 lead 的收尾对话,用户可基于交付结果做二次微调,lead 改完交付文件后用一个新工具刷新交付卡片。

**Architecture:** 继续对话复用现有 `sessionsApi.sendMessage(leadSessionId)`(后端零改动,走 kernel 标准 turn)。新增一个 lead-only MCP 工具 `update_deliverable`,以**追加 `deliverable_updated` 事件**的方式刷新交付卡片(沿用现有事件溯源,不改历史)。前端在完成态翻转布局:执行过程折叠、交付结果升主角、下接一段只显示 `task_completed` 之后 turns 的收尾对话区。

**Tech Stack:** 后端 Python 3.12 / FastAPI / SQLAlchemy(async)/ pytest;前端 React 19 / TypeScript / Zustand / Vitest;MCP 工具经 host toolkit server 暴露给 lead。

**参照设计:** `docs/superpowers/specs/2026-06-22-task-completion-continue-chat-design.md`

---

## 关键约束与已确认事实

- `finish_task` 完成后,lead kernel session 留在 `idle`、`mode=default`,`sessionsApi.sendMessage` 无任何状态阻止(只挡 `cancelled`/`archived`)。继续对话即普通 turn,与 task ActorRunner 无关。
- 交付卡片数据源是 **`task_completed` 事件的 payload `{summary, artifacts}`**(`TaskDetailPage.tsx:510-528`),不是 `result_manifest`。事件 append-only。
- **历史教训**(`TaskDetailPage.tsx:1158`):lead session 历史塞满了 plan/dispatch/await/review 编排噪音,直接全量暴露是"错误抽象"。因此收尾对话区**只渲染 `task_completed.created_at` 之后的 lead session turns**。
- `lead_session_id` 从 `detail.runs.find(r => r.kind === "lead")?.session_id` 取(`tasks-api.ts` 的 `TaskDetail = { task, runs, events }`)。
- MCP 工具不走 `api/openapi.yaml`;`TaskEvent.type` 是 `string`,新增事件类型无需改前端类型定义。

---

## 文件结构

**后端(host)**
- `backend/valuz_agent/modules/tasks/orchestrator.py` — 加 `update_deliverable(...)` 方法(追加 `deliverable_updated` 事件 + `completed` 校验)
- `backend/valuz_agent/modules/tasks/tools/declarations.py` — 加工具名常量、参数 schema、`ToolDef` 声明,纳入 `DISPATCH_TOOL_NAMES` + `DISPATCH_TOOL_DECLARATIONS`
- `backend/valuz_agent/modules/tasks/tools/handlers.py` — 加 `_update_deliverable_handler` + `register_tool`
- `backend/tests/modules/tasks/test_update_deliverable.py` — 新建测试

**前端**
- `frontend/packages/app/src/pages/task-detail/deliverable.ts` — 新建:纯函数 `deriveDeliverable(events)`(交付卡片数据派生,可单测)
- `frontend/packages/app/src/pages/task-detail/deliverable.test.ts` — 新建测试
- `frontend/packages/app/src/hooks/useLeadFollowUpChat.ts` — 新建:收尾对话 hook(自管理 events + SSE + send,按时间分界)
- `frontend/packages/app/src/hooks/useLeadFollowUpChat.test.ts` — 新建测试
- `frontend/packages/app/src/pages/TaskDetailPage.tsx` — 改:completionInfo 用纯函数、timeline 排除新事件、完成态布局翻转 + 收尾对话区接入
- `frontend/i18n/locales/zh-CN.json` / `en-US.json` — 加 key

---

## 后端

### Task 1: `orchestrator.update_deliverable` — 追加事件 + completed 校验

**Files:**
- Modify: `backend/valuz_agent/modules/tasks/orchestrator.py`(紧跟 `finish_task` 之后,约 1413 行)
- Test: `backend/tests/modules/tasks/test_update_deliverable.py`

- [ ] **Step 1: 写失败测试**

参照同目录现有 task 测试的 fixture 方式(grep `tests/modules/tasks/` 找一个已有 orchestrator 测试复制其 setup:建 project、commit 一个 task、拿到 lead_session_id、用 `finish_task` 置为 completed)。新建 `test_update_deliverable.py`:

```python
import pytest
from valuz_agent.modules.tasks.datastore import TaskEventDatastore
from valuz_agent.infra.db import async_unit_of_work

# 复用现有 conftest fixtures: orchestrator, a completed task with lead_session_id.
# (See an existing tests/modules/tasks/test_*.py for the exact fixture names and
#  reuse them verbatim — do NOT invent new fixtures.)

@pytest.mark.asyncio
async def test_update_deliverable_appends_event_on_completed_task(
    orchestrator, completed_task
):
    task_id, project_id, lead_session_id = completed_task

    result = await orchestrator.update_deliverable(
        task_id=task_id,
        project_id=project_id,
        lead_session_id=lead_session_id,
        summary="Revised summary",
        artifacts=["report.md"],
    )
    assert result == {"ok": True, "status": "updated"}

    async with async_unit_of_work() as db:
        events = await TaskEventDatastore(db).list_events(
            _user_id_for(completed_task), project_id=project_id, task_id=task_id
        )
    latest = [e for e in events if e.type == "deliverable_updated"]
    assert len(latest) == 1
    assert latest[0].payload["summary"] == "Revised summary"
    assert latest[0].payload["artifacts"] == ["report.md"]


@pytest.mark.asyncio
async def test_update_deliverable_rejected_on_active_task(
    orchestrator, active_task
):
    task_id, project_id, lead_session_id = active_task
    result = await orchestrator.update_deliverable(
        task_id=task_id,
        project_id=project_id,
        lead_session_id=lead_session_id,
        summary="x",
        artifacts=[],
    )
    assert result["status"] == "rejected"
    assert "completed" in result["error"]
```

> 注:`_user_id_for` / `list_events` 签名以现有测试与 `datastore.py` 为准;若现有测试用别的方式读事件,照搬那种方式。先 grep `def list_events` 与现有 task 测试确认。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/modules/tasks/test_update_deliverable.py -v`
Expected: FAIL — `AttributeError: 'TaskOrchestrator' object has no attribute 'update_deliverable'`

- [ ] **Step 3: 实现 `update_deliverable`**

在 `orchestrator.py` 的 `finish_task` 方法之后插入(复用该文件已 import 的 `async_unit_of_work` / `TaskDatastore` / `TaskEventDatastore` / `require_current_user_id`):

```python
    # ------------------------------------------------------------------
    # update_deliverable — refresh the deliverable card after the task is
    # completed (post-completion follow-up chat). Append-only: emits a
    # ``deliverable_updated`` event the detail page reads as the latest
    # deliverable, without mutating the original ``task_completed`` event.
    # Does NOT touch task status / plan / runs — the task stays completed.
    # ------------------------------------------------------------------

    async def update_deliverable(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        summary: str,
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)

            task_row = await task_ds.get_task_by_project(
                require_current_user_id(), project_id, task_id
            )
            if task_row is None:
                return {
                    "ok": False,
                    "error": "update_deliverable: task not found",
                    "status": "rejected",
                }
            if task_row.status != "completed":
                return {
                    "ok": False,
                    "error": (
                        "update_deliverable: task is "
                        f"{task_row.status!r}; only a 'completed' task can "
                        "refresh its deliverable card."
                    ),
                    "status": "rejected",
                }

            await event_ds.append_event(
                require_current_user_id(),
                project_id=project_id,
                task_id=task_id,
                type="deliverable_updated",
                actor=lead_session_id,
                session_id=lead_session_id,
                payload={"summary": summary, "artifacts": artifacts or []},
            )

        return {"ok": True, "status": "updated"}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/modules/tasks/test_update_deliverable.py -v`
Expected: PASS(两个测试)

- [ ] **Step 5: 提交**

```bash
git add backend/valuz_agent/modules/tasks/orchestrator.py backend/tests/modules/tasks/test_update_deliverable.py
git commit -m "feat(tasks): add orchestrator.update_deliverable (append deliverable_updated)"
```

---

### Task 2: `update_deliverable` MCP 工具 — 声明 + handler + 注册

**Files:**
- Modify: `backend/valuz_agent/modules/tasks/tools/declarations.py`
- Modify: `backend/valuz_agent/modules/tasks/tools/handlers.py`
- Test: `backend/tests/modules/tasks/test_update_deliverable.py`(追加 handler gate 测试)

- [ ] **Step 1: 写失败测试(handler lead-gate)**

在 `test_update_deliverable.py` 追加。参照现有 handler 测试如何构造 `ExecContext` 与 lead/non-lead session(grep `_check_lead_gate` 的现有测试)。骨架:

```python
@pytest.mark.asyncio
async def test_update_deliverable_handler_rejects_non_lead(non_lead_ctx):
    from valuz_agent.modules.tasks.tools.handlers import _resolve_update_deliverable_handler
    handler = _resolve_update_deliverable_handler()  # see note below
    res = await handler({"summary": "x", "artifacts": []}, non_lead_ctx)
    assert res.is_error
    assert "lead" in res.content
```

> 注:现有 handler 是 `register_dispatch_tools()` 内的闭包,测试通常通过注册后的 toolkit 调用而非直接拿闭包。**照搬现有 `_finish_task_handler` 的测试方式**(grep `finish_task` in `tests/`)——若现有测试是端到端经 toolkit 调用,就用同样方式;不要新造 `_resolve_*` 入口。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/modules/tasks/test_update_deliverable.py -v -k handler`
Expected: FAIL(工具/handler 未定义)

- [ ] **Step 3a: 在 `declarations.py` 加声明**

工具名常量区(约 60 行,`STOP_SUBTASK_TOOL_NAME` 之后)加:

```python
# Lead-only: after the task is completed, the lead may refresh the
# deliverable card (summary + artifacts) during follow-up chat. Appends a
# ``deliverable_updated`` event; does not change task status.
UPDATE_DELIVERABLE_TOOL_NAME = "update_deliverable"
```

`DISPATCH_TOOL_NAMES` 元组末尾加 `UPDATE_DELIVERABLE_TOOL_NAME,`。

参数 schema 区(与其它 `_*_PARAMETERS` 并列)加:

```python
_UPDATE_DELIVERABLE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "The refreshed deliverable summary shown on the task's "
                "deliverable card. Reflect whatever you just changed."
            ),
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Updated list of deliverable file paths (relative to the "
                "project working dir). Omit to keep the previous list."
            ),
        },
    },
    "required": ["summary"],
}
```

`ToolDef` 声明区(`FINISH_TASK_TOOL_DECLARATION` 附近)加:

```python
UPDATE_DELIVERABLE_TOOL_DECLARATION = ToolDef(
    name=UPDATE_DELIVERABLE_TOOL_NAME,
    description=(
        "Refresh the task's deliverable card after the task is COMPLETED. "
        "Call this when, during post-completion follow-up chat, you edited "
        "a deliverable file and want the card's summary/artifacts to reflect "
        "the latest state. Only valid on a completed task; it does NOT "
        "reopen the task, re-plan, or dispatch members."
    ),
    parameters=_UPDATE_DELIVERABLE_PARAMETERS,
    handler=None,
)
```

`DISPATCH_TOOL_DECLARATIONS` 元组里加入 `UPDATE_DELIVERABLE_TOOL_DECLARATION,`(在 `FINISH_TASK_TOOL_DECLARATION` 之后)。

- [ ] **Step 3b: 在 `handlers.py` 加 handler + 注册**

import 区把 `UPDATE_DELIVERABLE_TOOL_NAME` / `_UPDATE_DELIVERABLE_PARAMETERS` / `UPDATE_DELIVERABLE_TOOL_DECLARATION` 加到从 `declarations` 的 import 列表。

在 `register_dispatch_tools()` 内、`_finish_task_handler` 定义之后加闭包(紧贴现有 handler 风格):

```python
    async def _update_deliverable_handler(
        args: dict[str, Any], ctx: ExecContext
    ) -> ToolResult:
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate

        summary: str = args.get("summary", "")
        artifacts: list[str] = args.get("artifacts") or []

        try:
            result = await orchestrator.update_deliverable(
                task_id=task_id,
                project_id=project_id,
                lead_session_id=ctx.session_id,
                summary=summary,
                artifacts=artifacts,
            )
            if isinstance(result, dict) and result.get("status") == "rejected":
                return ToolResult(
                    content=result.get("error", "update_deliverable rejected"),
                    is_error=True,
                )
            return ToolResult(content="Deliverable card refreshed.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("update_deliverable handler error for task %s", task_id)
            return ToolResult(content=f"update_deliverable failed: {exc}", is_error=True)
```

在 `register_tool(...)` 调用区(`FINISH_TASK_TOOL_NAME` 注册之后)加:

```python
    register_tool(
        ToolDef(
            name=UPDATE_DELIVERABLE_TOOL_NAME,
            description=UPDATE_DELIVERABLE_TOOL_DECLARATION.description,
            parameters=_UPDATE_DELIVERABLE_PARAMETERS,
            handler=_update_deliverable_handler,
        )
    )
```

- [ ] **Step 4: 运行确认通过 + 全量 task 测试**

Run: `cd backend && uv run pytest tests/modules/tasks/ -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/valuz_agent/modules/tasks/tools/declarations.py backend/valuz_agent/modules/tasks/tools/handlers.py backend/tests/modules/tasks/test_update_deliverable.py
git commit -m "feat(tasks): expose update_deliverable as a lead-only MCP tool"
```

---

## 前端

### Task 3: `deriveDeliverable` 纯函数 — 取最新交付事件

**Files:**
- Create: `frontend/packages/app/src/pages/task-detail/deliverable.ts`
- Test: `frontend/packages/app/src/pages/task-detail/deliverable.test.ts`

- [ ] **Step 1: 写失败测试**

```typescript
import { describe, expect, it } from "vitest";
import { deriveDeliverable } from "./deliverable";
import type { TaskEvent } from "@valuz/core";

const ev = (over: Partial<TaskEvent>): TaskEvent => ({
  id: "e", sequence: 0, type: "kickoff", actor: "user",
  session_id: null, payload: {}, created_at: 0, ...over,
});

describe("deriveDeliverable", () => {
  it("returns null when no task_completed event", () => {
    expect(deriveDeliverable([ev({ type: "kickoff" })])).toBeNull();
  });

  it("reads summary/artifacts from task_completed", () => {
    const r = deriveDeliverable([
      ev({ type: "task_completed", created_at: 100,
           payload: { summary: "v1", artifacts: ["a.md"] } }),
    ]);
    expect(r).toEqual({ summary: "v1", artifacts: ["a.md"], completedAt: 100 });
  });

  it("prefers the latest deliverable_updated but keeps completedAt from task_completed", () => {
    const r = deriveDeliverable([
      ev({ type: "task_completed", created_at: 100,
           payload: { summary: "v1", artifacts: ["a.md"] } }),
      ev({ type: "deliverable_updated", created_at: 200,
           payload: { summary: "v2", artifacts: ["a.md", "b.md"] } }),
    ]);
    expect(r).toEqual({ summary: "v2", artifacts: ["a.md", "b.md"], completedAt: 100 });
  });

  it("ignores deliverable_updated with empty summary", () => {
    const r = deriveDeliverable([
      ev({ type: "task_completed", created_at: 100, payload: { summary: "v1", artifacts: [] } }),
      ev({ type: "deliverable_updated", created_at: 200, payload: { summary: "  ", artifacts: [] } }),
    ]);
    expect(r?.summary).toBe("v1");
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && pnpm vitest run packages/app/src/pages/task-detail/deliverable.test.ts`
Expected: FAIL — cannot find module `./deliverable`

- [ ] **Step 3: 实现纯函数**

```typescript
import type { TaskEvent } from "@valuz/core";

export interface DeliverableInfo {
  summary: string;
  artifacts: string[];
  completedAt: number;
}

const readPayload = (ev: TaskEvent): { summary: string; artifacts: string[] } => {
  const p = (ev.payload ?? {}) as { summary?: unknown; artifacts?: unknown };
  const summary = typeof p.summary === "string" ? p.summary.trim() : "";
  const artifacts = Array.isArray(p.artifacts)
    ? p.artifacts.filter((x): x is string => typeof x === "string")
    : [];
  return { summary, artifacts };
};

/**
 * Derive the deliverable card content. ``completedAt`` always comes from the
 * original ``task_completed`` event; summary/artifacts come from the latest
 * non-empty ``deliverable_updated`` (post-completion follow-up edits), falling
 * back to ``task_completed``.
 */
export const deriveDeliverable = (events: TaskEvent[]): DeliverableInfo | null => {
  const completed = events.find((e) => e.type === "task_completed");
  if (!completed) return null;

  let latest = readPayload(completed);
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].type === "deliverable_updated") {
      const upd = readPayload(events[i]);
      if (upd.summary) latest = upd;
      break;
    }
  }
  if (!latest.summary) return null;
  return { summary: latest.summary, artifacts: latest.artifacts, completedAt: completed.created_at };
};
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && pnpm vitest run packages/app/src/pages/task-detail/deliverable.test.ts`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/packages/app/src/pages/task-detail/deliverable.ts frontend/packages/app/src/pages/task-detail/deliverable.test.ts
git commit -m "feat(task-detail): deriveDeliverable reads latest deliverable_updated"
```

---

### Task 4: `useLeadFollowUpChat` hook — 收尾对话(SSE + send + 时间分界)

**Files:**
- Create: `frontend/packages/app/src/hooks/useLeadFollowUpChat.ts`
- Test: `frontend/packages/app/src/hooks/useLeadFollowUpChat.test.ts`

> 这是 ConversationPage 对话流逻辑的**精简版**:只做初始化 + SSE 增量 + 发送 + 按 `sinceTs` 时间分界过滤,故意不做向上翻页(收尾对话短)。复用 `buildTurns` / `useStableTurns` / `sessionsApi`。

- [ ] **Step 1: 写失败测试**

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

const listEvents = vi.fn();
const subscribeEvents = vi.fn();
const sendMessage = vi.fn();

vi.mock("@valuz/core", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    sessionsApi: { listEvents, subscribeEvents, sendMessage },
  };
});

import { useLeadFollowUpChat } from "./useLeadFollowUpChat";

const evt = (seq: number, ts: number, userText: string) => ({
  seq,
  timestamp: ts,
  event: { event_type: "message.user", payload: { text: userText } },
});

beforeEach(() => {
  listEvents.mockReset();
  subscribeEvents.mockReset();
  sendMessage.mockReset();
});

describe("useLeadFollowUpChat", () => {
  it("only keeps events after sinceTs", async () => {
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [evt(1, 50, "orchestration noise"), evt(2, 150, "follow-up question")],
    });
    subscribeEvents.mockResolvedValue(undefined);

    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 100 }),
    );

    await waitFor(() => expect(result.current.turns.length).toBe(1));
    expect(result.current.turns[0].userText).toBe("follow-up question");
  });

  it("send() forwards to sessionsApi.sendMessage and toggles sending", async () => {
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    subscribeEvents.mockResolvedValue(undefined);
    let resolveSend: () => void = () => {};
    sendMessage.mockImplementation(
      () => new Promise<void>((r) => { resolveSend = () => r(); }),
    );

    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());

    act(() => { void result.current.send("hello"); });
    await waitFor(() => expect(result.current.sending).toBe(true));
    expect(sendMessage).toHaveBeenCalledWith("s1", "hello");

    act(() => resolveSend());
    await waitFor(() => expect(result.current.sending).toBe(false));
  });

  it("no-ops when leadSessionId is null", () => {
    renderHook(() => useLeadFollowUpChat({ leadSessionId: null, sinceTs: 0 }));
    expect(listEvents).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && pnpm vitest run packages/app/src/hooks/useLeadFollowUpChat.test.ts`
Expected: FAIL — cannot find module `./useLeadFollowUpChat`

- [ ] **Step 3: 实现 hook**

```typescript
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  buildTurns,
  useStableTurns,
  sessionsApi,
  type SessionEventDTO,
} from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";

export interface LeadFollowUpChat {
  turns: ConversationTurn[];
  sending: boolean;
  send: (text: string) => Promise<void>;
}

/**
 * Minimal follow-up chat over a completed task's lead session. Loads the
 * session event history once, subscribes to the SSE stream for live updates,
 * and keeps only events strictly AFTER ``sinceTs`` (the ``task_completed``
 * timestamp) so the orchestration history above the finish line never leaks
 * into the user-facing follow-up conversation.
 */
export function useLeadFollowUpChat(params: {
  leadSessionId: string | null;
  sinceTs: number | null;
}): LeadFollowUpChat {
  const { leadSessionId, sinceTs } = params;
  const [events, setEvents] = useState<SessionEventDTO[]>([]);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    setEvents([]);
    if (!leadSessionId) return;
    const ac = new AbortController();
    let cancelled = false;

    void (async () => {
      try {
        const { items } = await sessionsApi.listEvents(leadSessionId, 0);
        if (cancelled) return;
        setEvents(items);
        const lastSeq = items.length ? items[items.length - 1].seq : 0;
        await sessionsApi.subscribeEvents(
          leadSessionId,
          (ev) => {
            if (!cancelled) setEvents((prev) => [...prev, ev]);
          },
          lastSeq,
          ac.signal,
        );
      } catch {
        // SSE drop / abort — surfaced as a still composer; nothing to recover.
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [leadSessionId]);

  const followUpEvents = useMemo(
    () =>
      sinceTs == null
        ? []
        : events.filter((e) => (e.timestamp ?? 0) > sinceTs),
    [events, sinceTs],
  );

  const rawTurns = useMemo(() => buildTurns(followUpEvents), [followUpEvents]);
  const turns = useStableTurns(rawTurns);

  const send = useCallback(
    async (text: string) => {
      if (!leadSessionId || !text.trim()) return;
      setSending(true);
      try {
        await sessionsApi.sendMessage(leadSessionId, text);
      } finally {
        setSending(false);
      }
    },
    [leadSessionId],
  );

  return { turns, sending, send };
}
```

> 执行期确认点(写完先验证,不要假设):①`SessionEventDTO` 是否带 `timestamp` 字段且历史事件都填了——若不可靠,改用 seq 锚点(在 Task 1 的 `task_completed` payload 里额外存 lead session 的 `anchor_seq`,前端按 `seq > anchor_seq` 过滤)。先 grep `interface SessionEventDTO` 在 `packages/core` 的定义确认。②`buildTurns` 的入参类型与 `listEvents` 返回的 `items` 元素类型一致(都应是 `SessionEventDTO`)。

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && pnpm vitest run packages/app/src/hooks/useLeadFollowUpChat.test.ts`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/packages/app/src/hooks/useLeadFollowUpChat.ts frontend/packages/app/src/hooks/useLeadFollowUpChat.test.ts
git commit -m "feat(task-detail): useLeadFollowUpChat hook for post-completion chat"
```

---

### Task 5: i18n key

**Files:**
- Modify: `frontend/i18n/locales/zh-CN.json`、`frontend/i18n/locales/en-US.json`

- [ ] **Step 1: 加 key**(`task.*` 命名空间)

zh-CN.json:
```json
"task.followUp.heading": "基于交付结果继续调整",
"task.followUp.placeholder": "跟 Lead 说要怎么调整…",
"task.followUp.runTimelineToggle": "执行过程",
"task.followUp.runTimelineExpand": "展开查看过程"
```

en-US.json(同 key):
```json
"task.followUp.heading": "Keep refining from the deliverable",
"task.followUp.placeholder": "Tell the lead what to adjust…",
"task.followUp.runTimelineToggle": "Run timeline",
"task.followUp.runTimelineExpand": "Expand to view the process"
```

- [ ] **Step 2: 重新生成类型**

Run: `cd backend && uv run python ../i18n/scripts/gen_types.py`
Expected: 无错误,`packages/shared/src/types/i18n.ts` 更新

- [ ] **Step 3: 提交**

```bash
git add frontend/i18n/locales/zh-CN.json frontend/i18n/locales/en-US.json frontend/packages/shared/src/types/i18n.ts backend/valuz_agent/generated/i18n_keys.py
git commit -m "i18n(task): add follow-up chat keys"
```

---

### Task 6: TaskDetailPage 完成态布局翻转 + 收尾对话区接入

**Files:**
- Modify: `frontend/packages/app/src/pages/TaskDetailPage.tsx`

- [ ] **Step 1: completionInfo 改用纯函数**

把 `completionInfo`(510-528 行)的 `useMemo` 体替换为:

```typescript
import { deriveDeliverable } from "./task-detail/deliverable";
// ...
const completionInfo = useMemo(
  () => deriveDeliverable(detail?.events ?? []),
  [detail],
);
```

(`DeliverableInfo` 字段 `summary`/`artifacts`/`completedAt` 与原内联类型一致,下游 853-957 渲染无需改。)

- [ ] **Step 2: timeline 排除 `deliverable_updated`**

定位执行过程 timeline 的事件源(`timelineNodes` 构建,grep `timelineNodes` / `EVENT_META`)。在喂给 timeline 的事件 filter 中排除新事件类型,避免收尾刷新冒进编排时间线:

```typescript
// where the timeline consumes events (e.g. const timelineEvents = events.filter(...))
.filter((e) => e.type !== "deliverable_updated")
```

若 `EVENT_META`(71-150)对未知 type 有兜底渲染,则无需为 `deliverable_updated` 加条目(它不进 timeline);确认兜底不会报错即可。

- [ ] **Step 3: 完成态——执行过程折叠 + 收尾对话区**

在 `TaskDetailPage` 顶部引入:

```typescript
import { useLeadFollowUpChat } from "../../hooks/useLeadFollowUpChat";
import { ConversationTurnList, Composer } from "@valuz/ui";
```

在组件内(`completionInfo` 之后)加派生与 hook(hooks 必须在任何 early-return 之前调用):

```typescript
const isCompleted = detail?.task.status === "completed";
const leadSessionId = useMemo(
  () => detail?.runs.find((r) => r.kind === "lead")?.session_id ?? null,
  [detail],
);
const followUp = useLeadFollowUpChat({
  leadSessionId: isCompleted ? leadSessionId : null,
  sinceTs: completionInfo?.completedAt ?? null,
});
const [followUpDraft, setFollowUpDraft] = useState("");
const followUpScrollRef = useRef<HTMLDivElement>(null);
const [runTimelineOpen, setRunTimelineOpen] = useState(false);

const handleFollowUpSend = useCallback(async () => {
  const text = followUpDraft.trim();
  if (!text) return;
  setFollowUpDraft("");
  await followUp.send(text);
  await loadData(); // pull any deliverable_updated the lead emitted this turn
}, [followUpDraft, followUp, loadData]);
```

在渲染里,完成态下:把现有"执行过程 timeline"段包进可折叠容器(默认 `runTimelineOpen=false`),并把交付结果卡片(853-957)移到 timeline 之后;在交付卡片之后追加收尾对话区。结构(用项目语义 token + i18n,遵循 `frontend/CLAUDE.md` UI 规范):

```tsx
{isCompleted && (
  <>
    {/* 折叠的执行过程 */}
    <button
      type="button"
      onClick={() => setRunTimelineOpen((v) => !v)}
      className="mt-4 flex w-full items-center gap-1.5 text-[12px] text-ink-meta hover:text-ink-heading"
    >
      <ChevronRight
        className={cn("h-3.5 w-3.5 transition-transform", runTimelineOpen && "rotate-90")}
      />
      <span>{t("task.followUp.runTimelineToggle")}</span>
      {!runTimelineOpen && (
        <span className="text-ink-muted">· {t("task.followUp.runTimelineExpand")}</span>
      )}
    </button>
    {runTimelineOpen && (
      <div className="mt-2">{/* 现有 timeline 段 JSX 移到这里 */}</div>
    )}

    {/* 交付结果卡片(现 853-957 的 JSX 移到这里,主角) */}
    {/* …deliverable card… */}

    {/* 收尾对话区 */}
    <section className="mt-6">
      <div className="mb-3 flex items-center gap-2 text-[12px] font-medium text-ink-heading">
        <span className="h-px flex-1 bg-surface-border" />
        {t("task.followUp.heading")}
        <span className="h-px flex-1 bg-surface-border" />
      </div>
      <div ref={followUpScrollRef} className="max-h-[480px] overflow-y-auto">
        <ConversationTurnList
          turns={followUp.turns}
          scrollContainerRef={followUpScrollRef}
          sending={followUp.sending}
          loading={false}
          error={null}
        />
      </div>
      <Composer
        value={followUpDraft}
        onChange={setFollowUpDraft}
        onSend={() => void handleFollowUpSend()}
        sending={followUp.sending}
        placeholder={t("task.followUp.placeholder")}
        autoFocus={false}
      />
    </section>
  </>
)}
```

> 执行期确认点:①`Composer`(`packages/ui`)的 props——只传文本相关(`value`/`onChange`/`onSend`/`sending`/`placeholder`),省略 model/runtime/skill/附件 props 时它应优雅降级为纯文本输入框;若 `Composer` 在缺省这些 props 时报错或显示空选择器,改用一个最小本地 textarea+发送按钮(遵循 UI 规范:`rounded-md`、`focus-visible:ring`)。先在浏览器看一眼渲染。②`ConversationTurnList` 的 `sending` 语义:仅在最后一个 turn 上画等待指示。③确认这些 hook(`useLeadFollowUpChat`、`useState`、`useMemo`)都在组件早返回(`if (loading)` / `if (!detail)`,706-717 行)**之前**调用,避免 hooks 顺序错误。

- [ ] **Step 4: 类型检查 + 浏览器验证**

Run: `cd frontend && pnpm typecheck`
Expected: PASS

然后按 `frontend/CLAUDE.md` 用浏览器验证(`./scripts/dev.sh`,完成一个任务,进详情页):
- 完成态底部出现输入框;执行过程默认折叠、可展开
- 发一条消息 → lead 流式回复出现在收尾对话区,且**不**夹带任务编排历史
- 让 lead 改一个交付文件并提示它刷新 → 交付卡片 summary/artifacts 更新
- 刷新页面 → 收尾对话仍在(time-anchored 持久)

- [ ] **Step 5: 提交**

```bash
git add frontend/packages/app/src/pages/TaskDetailPage.tsx
git commit -m "feat(task-detail): inline follow-up chat on completed tasks"
```

---

### Task 7: 全量验证

- [ ] **Step 1: 后端**

Run: `cd backend && uv run pytest && uv run mypy valuz_agent/ && uv run ruff check valuz_agent/`
Expected: all PASS

- [ ] **Step 2: 前端**

Run: `cd frontend && pnpm test && pnpm typecheck`
Expected: all PASS

- [ ] **Step 3: 仓库级质量门**

Run: `make test-all && make typecheck && make lint`
Expected: all PASS(CLAUDE.md 要求三者全绿才算完成)

- [ ] **Step 4: 浏览器回归**(release 前 UI 必验)

完整走一遍 Task 6 Step 4 的四条验证路径,确认无回归。

---

## 边界与非目标(实现时勿越界)

- 仅 `completed` 开放收尾对话;`active`/`paused`/`blocked`/`stopped`/`failed` 的现有底部栏(1163-1244)**保持不动**。
- 收尾对话期间 task 状态恒为 `completed`:不调 `resume_task`、不改 plan、不 dispatch。`update_deliverable` 只追加事件。
- 不做向上翻页、不做交付物版本 diff(YAGNI)。
- 不在收尾 Composer 暴露 agent/model/runtime/skill 切换(固定 lead)。

---

## Self-Review 记录

- **Spec 覆盖**:继续对话(send_message 复用→Task 6)、update_deliverable 工具(Task 1+2)、卡片刷新取最新事件(Task 3)、布局翻转(Task 6)、仅 completed(Task 1 校验 + Task 6 `isCompleted` 门)、不改 task 状态(Task 1 不动 status)、编排噪音不外泄(Task 4 时间分界)——均有对应任务。
- **新增的 spec 外约束**(收尾对话按 `task_completed` 时间分界):源于 `TaskDetailPage.tsx:1158` 的历史教训,已写入 Task 4 并在 spec 第 6.2 节语义内(只显示收尾对话)。
- **类型一致性**:`DeliverableInfo`{summary,artifacts,completedAt} 跨 Task 3/6 一致;`useLeadFollowUpChat` 返回 {turns,sending,send} 跨 Task 4/6 一致;`update_deliverable` 签名跨后端 Task 1/2 一致。
- **未决执行点**(已在对应 Task 标注,非占位):SessionEventDTO.timestamp 可靠性(否则退 seq 锚点)、Composer 缺省 props 的降级行为。两者都给了明确的 fallback,不是 TODO。
