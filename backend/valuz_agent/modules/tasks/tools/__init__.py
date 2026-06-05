"""Dispatch MCP tool surface, split into static declarations + thin handlers.

``declarations`` holds the import-safe, orchestrator-free surface (tool names,
JSON-schema parameter dicts, ``ToolDef(handler=None)`` declarations, and the
pure agent-config transforms ``strip_dispatch_tools`` /
``ensure_orchestration_tools_on_agent``). ``handlers`` holds
``register_dispatch_tools`` plus the lead/plan-writer gate helpers and the thin
async closure handlers that translate args → composition-root service call →
``ToolResult``.

``valuz_agent.modules.tasks.dispatch_mcp`` re-exports both so existing import
sites keep working unchanged.
"""
