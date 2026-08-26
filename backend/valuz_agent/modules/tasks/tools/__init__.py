"""Task MCP tool surface, split into static declarations + thin handlers.

``declarations`` holds the import-safe, orchestrator-free surface: tool names,
JSON-schema parameter dicts, ``ToolDef(handler=None)`` declarations, and the
two audience tuples (``DISPATCH_TOOL_DECLARATIONS`` = lead toolset,
``ORCHESTRATION_TOOL_DECLARATIONS`` = chat toolset) that ``boot/steps.py``
partitions the toolkit MCP server by.

``handlers`` holds ``build_task_tool_defs`` plus the lead / plan-writer /
orchestration gate wrappers and the thin async closure handlers that translate
args → composition-root service call → ``ToolResult``. The gate *policy* those
wrappers apply is pure and lives in ``gate``.
"""
