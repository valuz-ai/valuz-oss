"""Agent Plugins — the ``plugin`` install unit (skills + MCP servers).

Implements the Agent Plugins 1.0.0 consumer side (``manifest.py``: closed
``plugin.json`` schema, ``skills/`` discovery, ``mcp.json`` variants,
placeholder expansion, path containment) plus compatibility readers for the
Claude Code / WorkBuddy ``.claude-plugin`` / ``.codebuddy-plugin`` layouts,
and the local plugin library (``service.py``): install / preview / update /
enable / disable / reference-counted uninstall / export.

Design: ``docs/cloud-marketplace/design/agent-plugins-support.md`` (commercial
repo). Spec: https://agent-plugins.org/specification (1.0.0).
"""
