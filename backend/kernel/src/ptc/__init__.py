"""Programmatic Tool Calling (PTC) — code-face execution for data MCP tools.

The model writes Python that imports generated wrapper functions from a
materialized skill; the wrappers POST each tool call back to the kernel's
loopback PTC endpoint, which forwards to the upstream MCP server with the
session's real credentials. Only the program's stdout returns to the model.

Design: docs (ptc-demo repo) ``valuz-ptc-design.md``. Modules:

- ``interpreter``  — host ``python3`` availability probe (cached)
- ``execution_registry`` — one-shot execution tokens + per-run trace
- ``upstream``     — per-execution upstream MCP client pool (HTTP/SSE)
- ``results``      — MCP result unwrapping + trace entry construction
- ``executor``     — the ``execute_code`` ToolDef (subprocess runner)

The FastAPI transport lives in ``kernel/app/ptc_router.py`` (app layer).
"""
