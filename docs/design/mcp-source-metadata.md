# MCP source metadata

Valuz can consume source descriptors carried in an MCP `CallToolResult._meta`
entry without changing the producer's business result. The canonical extension
key is:

```text
dev.valuz/source-metadata
```

This is a Valuz extension, not an MCP or W3C standard. Producers keep all
business values in `content` or `structuredContent`; the descriptor contains
only integrity, resource, addressing, and provenance metadata. Producers must
not emit Valuz Evidence handles, Collection handles, citations, or validation
verdicts.

## Compatibility

The legacy key `cn.valuz/citation-source` remains readable for historical
messages and independently migrating producers. A result may contain either
key. If both keys are present, their canonical JSON values must be identical;
otherwise Valuz ignores both descriptors while preserving the business result.
New producers should emit only `dev.valuz/source-metadata`.

The descriptor remains SourceDescriptorV1. Valuz verifies the trusted MCP
connection, tool name, target, canonical result hash, resource schema, and JSON
Pointers before registering any private Evidence or Collection. Invalid or
unsupported metadata fails closed and never makes the tool call itself fail.

For structured-output compatibility, an MCP server may repeat the exact
``structuredContent`` JSON value in a text ``content`` block.  JSON decoders do
not retain number spellings such as ``1.0000``.  When the decoded text is
strictly identical to ``structuredContent``, Valuz may therefore verify the
descriptor hash from the text's canonical wire number spellings.  A mismatch,
duplicate JSON key, non-finite number, or non-JSON text is never accepted as a
hash substitute.

Claude's native MCP bridge keeps only model-visible ``content`` and drops
result-level ``_meta`` / ``structuredContent`` before ``PostToolUse``.  Valuz
therefore exposes each configured MCP through one transparent in-process proxy
that preserves the same descriptor in an internal content sidecar.  The hook
removes the sidecar before model delivery.  Routing is independent of server
and tool names; failed MCP results are never sidecar-wrapped because they cannot
create Evidence.

## Resource provenance

A resource can optionally describe origin, temporal meaning, and derivation:

```json
{
  "provenance": {
    "origin": {
      "status": "available",
      "scope": "item",
      "mapping": {
        "sourceName": "/source_original",
        "sourceUrl": "/source_url",
        "documentId": "/accession_number"
      }
    },
    "temporal": {"dataAsOf": "/meta/provenance/data_as_of"},
    "derivation": {
      "class": "extracted",
      "methodRef": {"id": "company-kpi-normalization", "revision": "v1"}
    }
  }
}
```

`origin.status` is `available`, `not-provided`, or `mixed`; `scope` is
`resource` or `item`. Mapping values are RFC 6901 JSON Pointers and never copies
of source values. Supported derivation classes are `direct`, `normalized`,
`extracted`, `aggregated`, and `calculated`.

When verified, Valuz preserves the MCP delivery service as `providerId` and can
map an independently addressable original publisher, URL, source ID, document
ID, and publication time into the private Evidence source. An absent origin is
represented explicitly and is never guessed from a delivery provider. A
calculated value becomes Calculation Evidence only when its inputs and method
are independently addressable under the existing Citation protocol.

This metadata does not alter the Citation trust boundary: the immutable result
snapshot and Evidence registry remain private to Valuz, and the model never
receives provider-side validation claims as trusted facts.
