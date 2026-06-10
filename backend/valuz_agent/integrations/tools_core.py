from __future__ import annotations

from valuz_agent.ports.tool_provider import RuntimeBuildContext, ToolDef


class CoreToolProvider:
    """Registers doc_search and list_doc_scope for projects."""

    @property
    def name(self) -> str:
        return "core"

    def is_available(self, context: RuntimeBuildContext) -> bool:
        return True

    def list_tools(self, context: RuntimeBuildContext) -> list[ToolDef]:
        tools: list[ToolDef] = []
        if context.project_kind == "project" and context.doc_scope_ids:
            tools.append(ToolDef(
                name="doc_search",
                description=(
                    "Search project-scoped documents. "
                    "Returns matching snippets from bound documents. "
                    "Optionally narrow scope with folder_ids or "
                    "document_ids."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query.",
                        },
                        "folder_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Folder IDs to narrow scope."
                            ),
                        },
                        "document_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Document IDs to narrow scope."
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Max results. Default 5.",
                        },
                    },
                    "required": ["query"],
                },
                handler=None,
                read_only=True,
                source="core",
                priority=0,
                sort_key="doc_search",
            ))
            tools.append(ToolDef(
                name="list_doc_scope",
                description=(
                    "List document scope tree for the project. "
                    "Shows KBs, folders, and documents. "
                    "Pass folder_id to explore a subtree."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "folder_id": {
                            "type": "string",
                            "description": (
                                "Folder ID to list."
                            ),
                        },
                    },
                },
                handler=None,
                read_only=True,
                source="core",
                priority=0,
                sort_key="list_doc_scope",
            ))
        return tools
