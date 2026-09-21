# Code 自动化运行时契约（python / shell）

`execution.kind="code"` 的自动化在**项目目录**里跑一个程序：没有 agent、没有模型、不消耗点数。
桌面端是本机子进程；云端是挂了项目目录的沙箱（项目目录在沙箱里是同一绝对路径，所以 `entry` 不用推送）。
在 web / worker 进程里永远不会执行用户程序——云端没绑执行器时 run 直接 `failed` / `AUTOMATION_CODE_EXECUTOR_UNAVAILABLE`。

## 启动方式

| runtime | 命令（工作目录 = 项目目录） |
|---|---|
| `python`（默认） | `python3 _valuz_runner.py --ctx ctx.json --entry <entry 绝对路径> --output output.json` |
| `shell` | `bash <entry 绝对路径>`（没有 bash 时用 sh） |

- 解释器：`python3`（或 `python`）取自 `PATH`，部署可用 `VALUZ_AUTOMATION_PYTHON` 指定；找不到 → `AUTOMATION_RUNTIME_UNAVAILABLE`。
- `_valuz_runner.py` 是平台自带、只依赖标准库的引导脚本，每次 run 复制进 run 目录；它 import 入口模块、调用 `run(ctx)`、
  把包装对象写成 `output.json`。入口文件所在目录会被加到 `sys.path` 最前面，同目录的辅助模块可直接 import。
- 环境是**最小化**的：只保留 `PATH` `HOME` `LANG` `LC_ALL` `TMPDIR` `TEMP` `TMP` `SYSTEMROOT`，外加
  `PYTHONUNBUFFERED=1` `PYTHONDONTWRITEBYTECODE=1` 和下面的 `VALUZ_AUTOMATION_*`。主机进程里的 token、数据库地址都**不会**进来。
- 进程有自己的进程组：超时（`timeout_seconds`，默认 600，上限 3600）或取消时整组被杀。

## 目录

```
<项目目录>/
  <entry 所在目录>/…                          你的代码，随项目走（git / 备份 / 沙箱挂载天然覆盖）
  .valuz/automations/<automation_id>/
    workspace/                                跨 run 持久的草稿区（ctx.workspaceDir）：缓存、游标、上次拉的原始数据
    runs/<run_id>/                            每 run 一个目录（ctx.runDir）
      ctx.json  input.json  output.json  stdout.log  stderr.log  files/  _valuz_runner.py
```

`.valuz/` 已被项目扫描忽略。run 目录只保留最近 20 个（run 行保留 100 条），所以要长期留的东西放 `workspace/`，不要放 `runs/`。

## `ctx`（`run(ctx)` 的唯一参数；也是 `ctx.json` / `$VALUZ_AUTOMATION_CTX_FILE` 的内容）

| 键 | 类型 | 含义 |
|---|---|---|
| `automationId` | string | 自动化 id |
| `automationName` | string | 自动化名称 |
| `runId` | string | 本次 run id |
| `projectId` | string | 所属项目 id |
| `projectDir` | string | 项目目录绝对路径（= 工作目录） |
| `workspaceDir` | string | `.valuz/automations/<id>/workspace` 绝对路径 |
| `runDir` | string | `.valuz/automations/<id>/runs/<run_id>` 绝对路径 |
| `input` | object / string / null | 本次的**有效输入**：json 契约 → 对象（`default` 已合并、已校验）；text → 字符串；none → null |
| `trigger.type` | string | `cron` / `interval` / `manual`（人点「立即运行」）/ `agent`（agent 调 `run`）/ `api` / `event` |
| `trigger.invokedBy` | string / null | `api` 触发时调用方的不透明标签 |
| `trigger.invokedBySessionId` | string / null | agent 触发时发起 `run` 的会话 id |
| `triggeredAt` | int | 触发时刻，epoch 毫秒 |
| `scheduledAt` | int / null | cron / interval 触发时 = `triggeredAt`；其他触发为 null |
| `timezone` | string | 该自动化的有效时区（IANA） |
| `locale` | string | 用户语言，如 `zh-CN` |
| `previous` | object / null | **最近一次 `success` 且带 artifact 的 run**：`{runId, completedAt, artifact}`；周期型增量工作从这里接着算。没有则 null |

## 环境变量

`VALUZ_AUTOMATION_ID` · `VALUZ_AUTOMATION_RUN_ID` · `VALUZ_AUTOMATION_PROJECT_DIR` · `VALUZ_AUTOMATION_RUN_DIR` ·
`VALUZ_AUTOMATION_WORKSPACE_DIR` · `VALUZ_AUTOMATION_CTX_FILE`（ctx.json）· `VALUZ_AUTOMATION_INPUT_FILE`（input.json，
只含有效输入）· `VALUZ_AUTOMATION_OUTPUT_FILE`（output.json）。

## 包装对象（程序的产出）

```json
{
  "artifact": { "summary": "3 rows as of 2026-09-19", "asOf": "2026-09-19", "rows": [] },
  "files": [
    { "sourcePath": "/abs/…/runs/<run_id>/files/report.csv", "name": "report.csv", "mimeType": "text/csv" }
  ]
}
```

- `artifact` 必须是 JSON 对象；声明了 `result.schema` 就按它校验；JSON 编码后 ≤ 1 MiB（大结果放 `files`）。
  `artifact.summary` 是字符串时会成为 run 的 `result_summary`（否则取 JSON 前 200 字）。
- `files` 可选。每项 `sourcePath` 必填（绝对路径，或相对 `runDir`），**必须落在 `runDir` 内**（放 `files/` 子目录最省事）；
  `name` 默认取文件名；`mimeType` 缺省按扩展名猜。限制：≤ 32 个、单个 ≤ 8 MiB、合计 ≤ 32 MiB。
  主机读回每个文件、登记为项目的产物行，`read_run` 的 `files` 里是 `{artifact_id, name, mime_type, size_bytes, error}`；
  某个文件登记失败**不会让 run 失败**——该项 `artifact_id` 为空、`error` 说明原因，`log_tail` 里也有一行 `[host] could not record file …`。
  **不要把路径 / 字节 / base64 塞进 artifact**——文件走 `files`。
- python：`run(ctx)` 的返回值就是包装对象；程序自己写到 `$VALUZ_AUTOMATION_OUTPUT_FILE` 的**非空、合法 JSON** 优先于返回值
  （大结果或文件型结果用这条路，免得在内存里放两份）。`run` 可以是 `async def`，会被 `asyncio.run` 执行。
- shell：脚本必须自己把包装 JSON 写到 `$VALUZ_AUTOMATION_OUTPUT_FILE`；没写 → `AUTOMATION_OUTPUT_INVALID`。stdout 只是日志。

## 退出码与 run 状态

| 情况 | 退出码 | run 状态 / `error_code` |
|---|---|---|
| 正常 | 0 | `success`（artifact 与 files 校验通过后） |
| 没定义 `run` / 返回不是对象 / 缺 `artifact` / `files` 项缺 `sourcePath` / 输出文件不是 JSON | 2 | `failed` / `AUTOMATION_CODE_EXIT`（`program exited with code 2`，原因在 stderr 的 `valuz-automation-runner:` 行） |
| 入口 import 或 `run(ctx)` 抛异常（traceback 在 stderr）；`sys.exit(n)` 保留 n | 3 / n | `failed` / `AUTOMATION_CODE_EXIT` |
| 退出 0 但 `output.json` 缺失或不是「带 `artifact` 对象的对象」 | 0 | `failed` / `AUTOMATION_OUTPUT_INVALID` |
| artifact 不符 `result.schema`、`files` 越界或超限 | 0 | `failed` / `AUTOMATION_ARTIFACT_INVALID`（消息带路径，如 `artifact.rows: …`） |
| 超过 `timeout_seconds` | — | `timeout` / `AUTOMATION_CODE_TIMEOUT` |
| 被 `cancel` | — | `cancelled` / `AUTOMATION_CANCELLED` |
| `entry` 逃出项目目录 / 文件不存在 | — | `failed` / `AUTOMATION_CODE_ENTRY_INVALID` / `AUTOMATION_CODE_ENTRY_MISSING` |
| 找不到解释器 / 云端没绑执行器 / 执行器自身出错 | — | `failed` / `AUTOMATION_RUNTIME_UNAVAILABLE` / `AUTOMATION_CODE_EXECUTOR_UNAVAILABLE` / `AUTOMATION_CODE_EXECUTOR_FAILED` |

stdout / stderr 各留尾部 64 KiB，合成 `read_run` 的 `log_tail`（stderr 段以 `--- stderr ---` 开头）；完整日志在 run 目录里。
`read_run` 的 `executor_ref` 记录在哪跑的（`local:<pid>` / `sandbox:<instance>`）。

## 完整示例：增量拉取 + 阈值判断 + 生成文件

```py
"""automations/price-watch/automation.py — 只用标准库。"""
import json
import os
import urllib.request


def _fetch(symbol: str) -> float:
    with urllib.request.urlopen(f"https://example.invalid/quote?s={symbol}", timeout=20) as resp:
        return float(json.load(resp)["last"])


def run(ctx):
    inp = ctx["input"] or {}
    symbol = inp["symbol"]                      # 由 input_contract.schema 保证存在
    threshold = float(inp.get("threshold", 0))
    prev = ctx["previous"]
    last_seen = prev["artifact"].get("price") if prev else None

    price = _fetch(symbol)
    breached = threshold and price >= threshold

    # 跨 run 的历史放 workspace（runs/ 只保留最近 20 个）
    history_path = os.path.join(ctx["workspaceDir"], "history.jsonl")
    with open(history_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": ctx["triggeredAt"], "price": price}) + "\n")

    # 本次要交付的文件放 runDir/files
    report = os.path.join(ctx["runDir"], "files", "report.json")
    with open(report, "w", encoding="utf-8") as fh:
        json.dump({"symbol": symbol, "price": price, "previous": last_seen}, fh)

    return {
        "artifact": {
            "summary": f"{symbol} {price} ({'breached' if breached else 'ok'})",
            "symbol": symbol,
            "price": price,
            "previous": last_seen,
            "breached": bool(breached),
            "asOf": ctx["triggeredAt"],
        },
        "files": [{"sourcePath": report, "name": "report.json", "mimeType": "application/json"}],
    }
```

配套契约：

```jsonc
input_contract: {"kind":"json",
                 "schema":{"type":"object","properties":{"symbol":{"type":"string"},"threshold":{"type":"number"}},
                           "required":["symbol"],"additionalProperties":false},
                 "default":{"threshold":0}}
execution:      {"kind":"code","runtime":"python","entry":"automations/price-watch/automation.py","timeout_seconds":120}
result:         {"kind":"artifact",
                 "schema":{"type":"object","properties":{"summary":{"type":"string"},"price":{"type":"number"}},
                           "required":["summary","price"]}}
```

验证：`run` + `input:{"symbol":"600519"}` + `wait_seconds:60` → `run.status=="success"`，`run.artifact.price` 是数字，
`run.files[0].name=="report.json"`。
