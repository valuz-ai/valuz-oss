# Trusted MCP Server Instructions

MCP servers may return an `instructions` string from the `initialize`
handshake. Valuz treats that string as server-controlled prompt text, not as
ordinary tool output.

## Trust boundary

Custom and marketplace MCP servers remain fully usable as tools, but their
initialize instructions are ignored. Instructions are accepted only when the
Host proves that the runtime connector matches a first-party built-in catalog
entry:

1. the persisted connector is marked `builtin`;
2. the same slug exists in the packaged or edition-contributed connector
   catalog and that entry explicitly declares `builtin: true`;
3. the persisted URL exactly matches the catalog URL, apart from a trailing
   slash.

The persisted `connector_type` is not sufficient by itself because connector
creation is a public API operation. A custom connector cannot gain prompt
authority by copying a built-in slug or type while pointing at another URL.

## Session lifecycle

When a session is created, the Host resolves the user's enabled MCP connectors
and marks catalog-pinned built-ins as trusted. The Kernel then:

1. initializes trusted HTTP/SSE servers concurrently;
2. reads at most 8,000 characters from each server and 16,000 characters in
   total;
3. attributes each block with the connector name and a `builtin` trust label;
4. appends the blocks to the session instructions and persists that immutable
   session snapshot.

Fetching is limited to five seconds and fails open. A missing, empty, invalid,
or temporarily unavailable Server Instructions response must not prevent the
session from being created. Untrusted servers are not contacted for prompt
text.

The instructions are frozen at session creation so all runtimes see the same
prompt and a later server change cannot silently rewrite an existing session.
Tools and OAuth credentials may still refresh through their existing runtime
paths; this does not mutate the frozen instruction snapshot.

## Authoring guidance for built-in servers

Server Instructions should describe stable server-wide usage guidance: how to
discover capabilities, how tool families relate, and which result fields are
authoritative. They should not duplicate every tool schema, include user data,
embed credentials, override the user's request, or claim that provider data has
already passed Valuz citation verification.

Source and citation metadata remain governed by
[MCP source metadata](mcp-source-metadata.md). Server Instructions can explain
how an agent should use the server, but only a verified source descriptor can
register Evidence.
