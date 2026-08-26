"""PTC codegen — wrapper modules, docs, and skill assembly for the code face.

Host-side counterpart of ``kernel/src/ptc``: discovers eligible tool schemas
from a session's data connectors, generates typed Python wrapper functions
plus per-tool markdown docs, and composes the deployable ``mcp_client.py``
(the static ``client_runtime`` source + a JSON config epilogue). The kernel
executes the result; nothing here runs agent code.
"""
