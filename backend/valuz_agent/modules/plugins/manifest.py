"""Agent Plugins 1.0.0 reader + compatibility readers + exporter.

Pure filesystem/JSON logic — no DB, no app state — so it is unit-testable in
isolation and reusable by the marketplace normalizers.

Three entry points:

* :func:`load_plugin_dir` — auto-detects the layout of a plugin directory and
  returns a :class:`LoadedPlugin`: the validated manifest, the discovered
  skills, the parsed MCP servers, per-component failures (with reasons) and
  non-fatal warnings. Layouts:

  - ``agent_plugins`` — root ``plugin.json`` (closed schema, ``$schema``
    pinned to 1.0.0), ``skills/<name>/SKILL.md`` one level deep, root
    ``mcp.json`` (``stdio`` / ``streamable-http`` / ``sse`` closed variants);
  - ``claude_plugin`` / ``codebuddy_plugin`` — ``.claude-plugin/plugin.json`` /
    ``.codebuddy-plugin/plugin.json`` (open manifest): skills from ``skills/``,
    from manifest-declared ``skills`` paths, and a root ``SKILL.md`` (the
    "plugin is itself one skill" WorkBuddy shape); MCP from ``.mcp.json``
    (Claude format, wrapped or bare) or inline ``mcpServers``; ``agents`` /
    ``commands`` / ``hooks`` / ``rules`` / LSP are ignored with a warning.
    Everything is normalized into the same Agent Plugins shapes so the rest of
    the pipeline never sees the legacy format.

* :func:`expand_placeholders` / :func:`resolve_stdio_launch` — the §9
  ``${PLUGIN_ROOT}`` / ``${PLUGIN_DATA}`` expansion and the §7.2.1 ``command``
  / ``cwd`` resolution + containment.

* :func:`build_export_zip` — writes an installed plugin back out in the Agent
  Plugins layout (root ``plugin.json`` + ``skills/`` + ``mcp.json``); anything
  Valuz-specific lives under the ``io.valuz.agent`` extension namespace.

Failure boundaries follow spec §4.1 / §11.3: a fatal manifest problem raises
:class:`PluginManifestError` (the caller rejects the plugin); everything else
is isolated to the component (type / skill / server) and reported.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from valuz_agent.integrations.skills_filesystem import _extract_frontmatter, _read_text

PLUGIN_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
SPEC_VERSION = "1.0.0"
VALUZ_EXTENSION_NS = "io.valuz.agent"

PluginFormat = Literal["agent_plugins", "claude_plugin", "codebuddy_plugin"]
McpTransport = Literal["stdio", "streamable-http", "sse"]

_MANIFEST_FIELDS = frozenset(
    {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
)
_AUTHOR_FIELDS = frozenset({"name", "email", "url"})
_STDIO_FIELDS = frozenset({"type", "command", "args", "env", "cwd"})
_REMOTE_FIELDS = frozenset({"type", "url", "headers"})
_RESERVED_ENV = frozenset({"PLUGIN_ROOT", "PLUGIN_DATA"})

# §5.5 — 1..64 chars, lowercase a-z0-9.-, alnum at both ends, no "--" / "..".
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
# Agent Skills — 1..64 chars, lowercase a-z0-9-, no leading/trailing "-", no "--".
SKILL_NAME_RE = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_PLACEHOLDER_RE = re.compile(r"\$\{PLUGIN_ROOT\}|\$\{PLUGIN_DATA\}")
# Legacy (Claude Code / WorkBuddy) plugin-root placeholders.
_LEGACY_ROOT_RE = re.compile(r"\$\{(?:CLAUDE|CODEBUDDY|WORKBUDDY)_PLUGIN_ROOT\}")
_LEGACY_ENV_REF_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

LEGACY_MANIFEST_DIRS: dict[str, PluginFormat] = {
    ".claude-plugin": "claude_plugin",
    ".codebuddy-plugin": "codebuddy_plugin",
}
# OS / editor junk that is never plugin content (excluded from hashing, copying
# and export so a Finder visit to the library can't flag a member as modified).
_JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "__MACOSX"})
# Top-level entries of a legacy plugin that are NOT part of a root skill.
_LEGACY_ROOT_SKILL_IGNORE = frozenset(
    {
        ".claude-plugin",
        ".codebuddy-plugin",
        ".mcp.json",
        "agents",
        "commands",
        "hooks",
        "rules",
        "skills",
        ".git",
        ".github",
        "node_modules",
    }
)


class PluginManifestError(ValueError):
    """Fatal manifest problem — the plugin MUST be rejected (§5.2 / §5.3)."""


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class PluginAuthorSpec:
    name: str | None = None
    email: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            k: v for k, v in (("name", self.name), ("email", self.email), ("url", self.url)) if v
        }


@dataclass
class PluginManifest:
    """The validated, closed ``plugin.json`` object."""

    name: str
    version: str | None = None
    description: str | None = None
    author: PluginAuthorSpec | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: list[str] = field(default_factory=list)
    extensions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Render back to the canonical Agent Plugins ``plugin.json`` object."""
        out: dict[str, Any] = {"$schema": PLUGIN_SCHEMA_ID, "name": self.name}
        if self.version is not None:
            out["version"] = self.version
        if self.description is not None:
            out["description"] = self.description
        if self.author is not None and self.author.to_dict():
            out["author"] = self.author.to_dict()
        if self.homepage is not None:
            out["homepage"] = self.homepage
        if self.repository is not None:
            out["repository"] = self.repository
        if self.license is not None:
            out["license"] = self.license
        if self.keywords:
            out["keywords"] = list(self.keywords)
        if self.extensions:
            out["extensions"] = json.loads(json.dumps(self.extensions))
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginManifest:
        """Rebuild from a previously rendered :meth:`to_dict` object (lenient —
        the stored manifest was validated when installed)."""
        author_raw = data.get("author")
        author = None
        if isinstance(author_raw, dict):
            author = PluginAuthorSpec(
                name=_opt_str(author_raw.get("name")),
                email=_opt_str(author_raw.get("email")),
                url=_opt_str(author_raw.get("url")),
            )
        keywords_raw = data.get("keywords")
        ext_raw = data.get("extensions")
        return cls(
            name=str(data.get("name") or ""),
            version=_opt_str(data.get("version")),
            description=_opt_str(data.get("description")),
            author=author,
            homepage=_opt_str(data.get("homepage")),
            repository=_opt_str(data.get("repository")),
            license=_opt_str(data.get("license")),
            keywords=[str(k) for k in keywords_raw] if isinstance(keywords_raw, list) else [],
            extensions={str(k): dict(v) for k, v in ext_raw.items() if isinstance(v, dict)}
            if isinstance(ext_raw, dict)
            else {},
        )


@dataclass
class SkillSpec:
    """One discovered skill inside a plugin package."""

    slug: str
    path: Path
    name: str
    description: str
    meta_version: str | None
    content_hash: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    # Top-level entries to leave out when copying/hashing — only set for the
    # legacy "root SKILL.md" shape where the skill dir IS the plugin root.
    ignore_names: frozenset[str] = frozenset()
    # The frontmatter ``name`` as authored when it differed from the directory
    # name (the identity): the installer rewrites it to ``slug`` and
    # ``content_hash`` already reflects the corrected SKILL.md.
    original_name: str | None = None

    @property
    def name_rewritten(self) -> bool:
        return self.original_name is not None


@dataclass
class McpServerSpec:
    """One normalized ``mcp.json`` server entry (portable form — placeholders
    NOT expanded; expansion happens at install per plugin instance)."""

    name: str
    type: McpTransport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        if self.type == "stdio":
            out: dict[str, Any] = {"type": "stdio", "command": self.command}
            if self.args:
                out["args"] = list(self.args)
            if self.env:
                out["env"] = dict(self.env)
            if self.cwd is not None:
                out["cwd"] = self.cwd
            return out
        out = {"type": self.type, "url": self.url}
        if self.headers:
            out["headers"] = dict(self.headers)
        return out

    @property
    def content_hash(self) -> str:
        return hash_json(self.to_dict())


@dataclass
class ComponentFailure:
    """A component-level failure the loader isolated (spec §11.3)."""

    kind: Literal["skill", "connector", "skills", "mcp"]
    ref: str
    reason: str


@dataclass
class LoadedPlugin:
    root: Path
    format: PluginFormat
    manifest: PluginManifest
    skills: list[SkillSpec] = field(default_factory=list)
    servers: list[McpServerSpec] = field(default_factory=list)
    skipped: list[ComponentFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def composition(self) -> Literal["skills_only", "with_connectors"]:
        return "with_connectors" if self.servers else "skills_only"

    def mcp_config(self) -> dict[str, Any] | None:
        """The normalized ``mcp.json`` object (``None`` when there are no
        servers)."""
        if not self.servers:
            return None
        return {
            "$schema": MCP_SCHEMA_ID,
            "mcpServers": {s.name: s.to_dict() for s in self.servers},
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def hash_json(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def hash_directory(
    root: Path,
    *,
    ignore_names: frozenset[str] = frozenset(),
    overrides: dict[str, bytes] | None = None,
) -> str:
    """Stable content hash of every regular file under ``root`` (posix
    relative path + bytes, NUL-separated — the same recipe as the skill
    staging hash so values coincide where both are computed). ``overrides``
    substitutes the bytes of the given relative paths (used to hash a skill
    AS IT WILL BE INSTALLED, i.e. with a rewritten SKILL.md, without touching
    the source tree)."""
    h = hashlib.sha256()
    for path in _iter_contained_files(root, ignore_names=ignore_names):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if overrides is not None and rel in overrides:
            h.update(overrides[rel])
        else:
            h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def is_within(base: Path, target: Path) -> bool:
    """``target`` (filesystem-resolved) stays inside ``base`` (resolved)."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _walk_contained(
    root: Path, *, ignore_names: frozenset[str] = frozenset()
) -> list[tuple[Path, Path, bool]]:
    """Depth-first ``(path, relative, is_dir)`` listing of ``root``.

    Symlinks are followed ONLY when their target resolves inside the resolved
    ``root`` (spec §4.1) — escaping links are recorded as ``is_dir=False``
    entries whose resolved path is outside, so callers can report and skip
    them; a directory cycle is broken with a visited set.
    """
    out: list[tuple[Path, Path, bool]] = []
    resolved_root = root.resolve()
    visited: set[Path] = set()

    def _inside(path: Path) -> bool:
        try:
            path.resolve().relative_to(resolved_root)
            return True
        except (ValueError, OSError):
            return False

    def _visit(directory: Path) -> None:
        try:
            key = directory.resolve()
        except OSError:
            return
        if key in visited:
            return
        visited.add(key)
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            rel = entry.relative_to(root)
            if entry.name in _JUNK_NAMES or (rel.parts and rel.parts[0] in ignore_names):
                continue
            if not _inside(entry):
                out.append((entry, rel, False))
                continue
            if entry.is_dir():
                out.append((entry, rel, True))
                _visit(entry)
            elif entry.is_file():
                out.append((entry, rel, False))

    _visit(root)
    return out


def _iter_contained_files(root: Path, *, ignore_names: frozenset[str] = frozenset()) -> list[Path]:
    """Every regular file under ``root`` whose resolved path stays inside the
    resolved ``root`` — symlinks escaping the tree are silently excluded (spec
    §4.1: deny access to escaping package paths)."""
    resolved_root = root.resolve()
    files: list[Path] = []
    for path, _rel, is_dir in _walk_contained(root, ignore_names=ignore_names):
        if is_dir or not path.is_file():
            continue
        try:
            path.resolve().relative_to(resolved_root)
        except (ValueError, OSError):
            continue
        files.append(path)
    return files


def copy_tree_contained(
    src: Path, dst: Path, *, ignore_names: frozenset[str] = frozenset()
) -> list[str]:
    """Copy ``src`` into ``dst`` (created), following symlinks only when they
    resolve inside ``src``. Returns warnings for skipped escaping entries."""
    warnings: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    resolved_src = src.resolve()
    for entry, rel, is_dir in _walk_contained(src, ignore_names=ignore_names):
        try:
            entry.resolve().relative_to(resolved_src)
        except (ValueError, OSError):
            warnings.append(f"skipped '{rel.as_posix()}': resolves outside the plugin root")
            continue
        target = dst / rel
        if is_dir:
            target.mkdir(parents=True, exist_ok=True)
        elif entry.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry, target)
            try:
                shutil.copymode(entry, target)
            except OSError:
                pass
    return warnings


def validate_plugin_name(name: object) -> str | None:
    """``None`` when valid, else the reason (spec §5.5)."""
    if not isinstance(name, str):
        return "name must be a string"
    if not name:
        return "name must not be empty"
    if len(name) > 64:
        return "name must be at most 64 characters"
    if not PLUGIN_NAME_RE.match(name):
        return (
            "name must use lowercase letters, digits, '-' or '.', start and end with a "
            "letter or digit, and contain no '--' or '..'"
        )
    return None


def validate_skill_name(name: object) -> str | None:
    """``None`` when valid, else the reason (Agent Skills ``name`` rules)."""
    if not isinstance(name, str) or not name:
        return "skill name must be a non-empty string"
    if len(name) > 64:
        return "skill name must be at most 64 characters"
    if not SKILL_NAME_RE.match(name):
        return (
            "skill name must use lowercase letters, digits and '-', not start or end "
            "with '-', and contain no '--'"
        )
    return None


# ---------------------------------------------------------------------------
# plugin.json (Agent Plugins §5)
# ---------------------------------------------------------------------------


def parse_plugin_manifest(data: object) -> tuple[PluginManifest, list[str]]:
    """Validate a ``plugin.json`` object against the closed 1.0.0 schema.

    Returns ``(manifest, warnings)``. Unknown top-level fields and a non-object
    ``extensions`` are reported and ignored (non-fatal per §5.2 / §8.1); every
    other violation raises :class:`PluginManifestError`.
    """
    if not isinstance(data, dict):
        raise PluginManifestError("plugin.json must contain a JSON object")
    warnings: list[str] = []
    schema = data.get("$schema")
    if schema is None:
        raise PluginManifestError("plugin.json is missing the required '$schema' field")
    if schema != PLUGIN_SCHEMA_ID:
        raise PluginManifestError(
            f"unsupported plugin manifest schema {schema!r} (expected {PLUGIN_SCHEMA_ID})"
        )
    if "name" not in data:
        raise PluginManifestError("plugin.json is missing the required 'name' field")
    name_error = validate_plugin_name(data.get("name"))
    if name_error is not None:
        raise PluginManifestError(f"invalid plugin name: {name_error}")
    for key in data:
        if key not in _MANIFEST_FIELDS:
            warnings.append(f"plugin.json: unknown top-level field '{key}' ignored")

    def _str_field(key: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise PluginManifestError(f"plugin.json: '{key}' must be a string")
        return value

    author: PluginAuthorSpec | None = None
    author_raw = data.get("author")
    if author_raw is not None:
        if not isinstance(author_raw, dict):
            raise PluginManifestError("plugin.json: 'author' must be an object")
        for key, value in author_raw.items():
            if key not in _AUTHOR_FIELDS:
                raise PluginManifestError(f"plugin.json: unknown 'author' field '{key}'")
            if not isinstance(value, str):
                raise PluginManifestError(f"plugin.json: 'author.{key}' must be a string")
        author = PluginAuthorSpec(
            name=author_raw.get("name"), email=author_raw.get("email"), url=author_raw.get("url")
        )

    keywords: list[str] = []
    keywords_raw = data.get("keywords")
    if keywords_raw is not None:
        if not isinstance(keywords_raw, list) or not all(isinstance(k, str) for k in keywords_raw):
            raise PluginManifestError("plugin.json: 'keywords' must be an array of strings")
        keywords = list(keywords_raw)

    extensions: dict[str, dict[str, Any]] = {}
    ext_raw = data.get("extensions")
    if ext_raw is not None:
        if not isinstance(ext_raw, dict):
            warnings.append("plugin.json: 'extensions' is not an object — ignored")
        else:
            for ns, value in ext_raw.items():
                if isinstance(value, dict):
                    extensions[str(ns)] = value
                else:
                    warnings.append(f"plugin.json: extension '{ns}' is not an object — ignored")

    manifest = PluginManifest(
        name=str(data["name"]),
        version=_str_field("version"),
        description=_str_field("description"),
        author=author,
        homepage=_str_field("homepage"),
        repository=_str_field("repository"),
        license=_str_field("license"),
        keywords=keywords,
        extensions=extensions,
    )
    return manifest, warnings


# ---------------------------------------------------------------------------
# Placeholders (§9.2) and stdio launch resolution (§7.2.1)
# ---------------------------------------------------------------------------


def expand_placeholders(value: str, *, plugin_root: str, plugin_data: str) -> str:
    """Single, non-recursive textual replacement of ``${PLUGIN_ROOT}`` /
    ``${PLUGIN_DATA}``. Other placeholder-like text stays literal."""
    mapping = {"${PLUGIN_ROOT}": plugin_root, "${PLUGIN_DATA}": plugin_data}
    return _PLACEHOLDER_RE.sub(lambda m: mapping[m.group(0)], value)


def _validate_command_form(command: object) -> str | None:
    if not isinstance(command, str) or not command:
        return "'command' must be a non-empty string"
    if any(ch.isspace() for ch in command):
        return "'command' must be a single executable token (no shell command line)"
    if command.startswith("./"):
        return None
    if "/" in command or "\\" in command or command.startswith("."):
        return "'command' must be a bare executable name or a plugin-relative './' path"
    if "${" in command:
        return "'command' does not support placeholder expansion"
    return None


def _validate_cwd_form(cwd: object) -> str | None:
    if not isinstance(cwd, str) or not cwd:
        return "'cwd' must be a non-empty string"
    if cwd.startswith("./"):
        return None
    for placeholder in ("${PLUGIN_ROOT}", "${PLUGIN_DATA}"):
        if cwd == placeholder or cwd.startswith(placeholder + "/"):
            return None
    return "'cwd' must be './'-relative, '${PLUGIN_ROOT}' or '${PLUGIN_DATA}' rooted"


@dataclass
class ResolvedLaunch:
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str


def resolve_stdio_launch(
    spec: McpServerSpec, *, plugin_root: Path, plugin_data: Path
) -> tuple[ResolvedLaunch | None, str | None]:
    """Turn a portable stdio entry into a concrete launch spec for THIS install.

    Expands placeholders in ``args`` / ``env`` / ``cwd``, resolves a
    plugin-relative ``command`` to an absolute path inside the plugin root,
    defaults ``cwd`` to the plugin root, enforces containment (root-rooted →
    inside root, data-rooted → inside data dir) and overlays the reserved
    ``PLUGIN_ROOT`` / ``PLUGIN_DATA`` variables. Returns ``(launch, None)`` or
    ``(None, reason)`` when the entry is invalid for this instance.
    """
    root_str = str(plugin_root)
    data_str = str(plugin_data)
    command = spec.command or ""
    if command.startswith("./"):
        target = plugin_root / command[2:]
        if not is_within(plugin_root, target):
            return None, "'command' resolves outside the plugin root"
        command = str(target)
    args = [expand_placeholders(a, plugin_root=root_str, plugin_data=data_str) for a in spec.args]
    env = {
        k: expand_placeholders(v, plugin_root=root_str, plugin_data=data_str)
        for k, v in spec.env.items()
    }
    if spec.cwd is None:
        cwd_path = plugin_root
    else:
        expanded = expand_placeholders(spec.cwd, plugin_root=root_str, plugin_data=data_str)
        if spec.cwd.startswith("./"):
            cwd_path = plugin_root / spec.cwd[2:]
            base = plugin_root
        elif spec.cwd.startswith("${PLUGIN_DATA}"):
            cwd_path = Path(expanded)
            base = plugin_data
        else:
            cwd_path = Path(expanded)
            base = plugin_root
        if not is_within(base, cwd_path):
            return None, "'cwd' resolves outside its permitted root"
    env["PLUGIN_ROOT"] = root_str
    env["PLUGIN_DATA"] = data_str
    return ResolvedLaunch(command=command, args=args, env=env, cwd=str(cwd_path)), None


# ---------------------------------------------------------------------------
# mcp.json (§7.2)
# ---------------------------------------------------------------------------


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _validate_remote_url(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return "'url' must be a non-empty string"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return "'url' must be an absolute http(s) URL"
    if parts.username is not None or parts.password is not None:
        return "'url' must not contain user information"
    if parts.fragment:
        return "'url' must not contain a fragment"
    if parts.scheme == "http" and not _is_loopback_host(parts.hostname or ""):
        return "'url' must use https for non-loopback endpoints"
    return None


def _validate_headers(headers: object) -> str | None:
    if not isinstance(headers, dict):
        return "'headers' must be an object of strings"
    seen: set[str] = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not _HEADER_NAME_RE.match(name):
            return f"invalid header name {name!r}"
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            return f"invalid header value for {name!r}"
        lowered = name.lower()
        if lowered in seen:
            return f"duplicate header {name!r} (case-insensitive)"
        seen.add(lowered)
    return None


def _validate_string_list(value: object, label: str) -> str | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return f"'{label}' must be an array of strings"
    return None


def _validate_string_map(value: object, label: str) -> str | None:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        return f"'{label}' must be an object of strings"
    return None


def parse_mcp_server(name: str, entry: object) -> tuple[McpServerSpec | None, str | None]:
    """Validate ONE closed server variant. ``(spec, None)`` or ``(None, reason)``."""
    if not isinstance(entry, dict):
        return None, "server entry must be an object"
    server_type = entry.get("type")
    if server_type == "stdio":
        unknown = set(entry) - _STDIO_FIELDS
        if unknown:
            return None, f"unknown stdio field(s): {', '.join(sorted(unknown))}"
        reason = _validate_command_form(entry.get("command"))
        if reason:
            return None, reason
        args_raw = entry.get("args", [])
        reason = _validate_string_list(args_raw, "args")
        if reason:
            return None, reason
        env_raw = entry.get("env", {})
        reason = _validate_string_map(env_raw, "env")
        if reason:
            return None, reason
        if any(k in _RESERVED_ENV for k in env_raw):
            return None, "'env' must not define PLUGIN_ROOT or PLUGIN_DATA"
        cwd_raw = entry.get("cwd")
        if cwd_raw is not None:
            reason = _validate_cwd_form(cwd_raw)
            if reason:
                return None, reason
        return (
            McpServerSpec(
                name=name,
                type="stdio",
                command=str(entry["command"]),
                args=list(args_raw),
                env=dict(env_raw),
                cwd=cwd_raw,
            ),
            None,
        )
    if server_type in ("streamable-http", "sse"):
        unknown = set(entry) - _REMOTE_FIELDS
        if unknown:
            return None, f"unknown {server_type} field(s): {', '.join(sorted(unknown))}"
        reason = _validate_remote_url(entry.get("url"))
        if reason:
            return None, reason
        headers_raw = entry.get("headers", {})
        reason = _validate_headers(headers_raw)
        if reason:
            return None, reason
        return (
            McpServerSpec(
                name=name,
                type="streamable-http" if server_type == "streamable-http" else "sse",
                url=str(entry["url"]),
                headers=dict(headers_raw),
            ),
            None,
        )
    if server_type is None:
        return None, "missing 'type'"
    return None, f"unknown transport type {server_type!r}"


def parse_mcp_config(
    data: object,
) -> tuple[list[McpServerSpec], list[ComponentFailure], str | None]:
    """Parse a whole ``mcp.json`` object.

    Returns ``(servers, per-server failures, top-level failure)``. A top-level
    failure (bad JSON shape, wrong/missing ``$schema``, extra fields) disables
    MCP for the plugin — the caller reports it and continues with skills.
    """
    if not isinstance(data, dict):
        return [], [], "mcp.json must contain a JSON object"
    unknown = set(data) - {"$schema", "mcpServers"}
    if unknown:
        return [], [], f"mcp.json: unknown top-level field(s): {', '.join(sorted(unknown))}"
    schema = data.get("$schema")
    if schema != MCP_SCHEMA_ID:
        return [], [], f"mcp.json: unsupported or missing '$schema' (expected {MCP_SCHEMA_ID})"
    servers_raw = data.get("mcpServers")
    if not isinstance(servers_raw, dict):
        return [], [], "mcp.json: 'mcpServers' must be an object"
    servers: list[McpServerSpec] = []
    failures: list[ComponentFailure] = []
    for name, entry in servers_raw.items():
        spec, reason = parse_mcp_server(str(name), entry)
        if spec is None:
            failures.append(ComponentFailure("connector", str(name), reason or "invalid"))
        else:
            servers.append(spec)
    return servers, failures, None


# ---------------------------------------------------------------------------
# Skills (§7.1)
# ---------------------------------------------------------------------------


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


_FRONTMATTER_NAME_LINE_RE = re.compile(r"^name\s*:")


def rewrite_frontmatter_name(raw: str, slug: str) -> str:
    """Return ``raw`` (a SKILL.md) with the frontmatter ``name`` set to ``slug``.

    Minimal textual edit — every other frontmatter line and the body are kept
    byte-for-byte (line endings normalized to ``\n``). A block-scalar ``name``
    (``name: |`` / ``name: >``) is collapsed to the plain slug. Without a
    frontmatter block the text is returned unchanged.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return raw
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return raw
    lines = text[4:closing].split("\n")
    out: list[str] = []
    replaced = False
    skipping_block = False
    for line in lines:
        if skipping_block:
            if line.strip() == "" or line.startswith((" ", "\t")):
                continue
            skipping_block = False
        if not replaced and _FRONTMATTER_NAME_LINE_RE.match(line):
            value = line.split(":", 1)[1].strip()
            out.append(f"name: {slug}")
            replaced = True
            skipping_block = value.startswith(("|", ">"))
            continue
        out.append(line)
    if not replaced:
        out.insert(0, f"name: {slug}")
    return "---\n" + "\n".join(out) + text[closing:]


def normalize_skill_dir(skill_dir: Path, slug: str) -> str | None:
    """Rewrite ``skill_dir/SKILL.md``'s frontmatter ``name`` to ``slug`` when
    it differs. Returns the original name when a rewrite happened, else
    ``None`` (also when there is no usable frontmatter)."""
    manifest_path = skill_dir / "SKILL.md"
    if not manifest_path.is_file():
        return None
    raw = _read_text(manifest_path)
    metadata, _body = _extract_frontmatter(raw)
    fm_name = metadata.get("name")
    if not isinstance(fm_name, str) or fm_name.strip() == slug:
        return None
    manifest_path.write_text(rewrite_frontmatter_name(raw, slug), encoding="utf-8")
    return fm_name.strip()


def read_skill_spec(
    skill_dir: Path,
    *,
    slug: str | None = None,
    ignore_names: frozenset[str] = frozenset(),
) -> tuple[SkillSpec | None, str | None, list[str]]:
    """Read + validate one skill directory. ``(spec, None, warnings)`` or
    ``(None, reason, warnings)`` when the skill must be skipped."""
    warnings: list[str] = []
    dir_slug = slug or skill_dir.name
    manifest_path = skill_dir / "SKILL.md"
    if not manifest_path.is_file():
        return None, "missing SKILL.md", warnings
    name_error = validate_skill_name(dir_slug)
    if name_error is not None:
        return None, f"invalid skill directory name: {name_error}", warnings
    try:
        raw = _read_text(manifest_path)
    except OSError as exc:
        return None, f"cannot read SKILL.md: {exc}", warnings
    metadata, _body = _extract_frontmatter(raw)
    fm_name = metadata.get("name")
    fm_desc = metadata.get("description")
    if not isinstance(fm_name, str) or not fm_name.strip():
        return None, "SKILL.md frontmatter is missing 'name'", warnings
    if not isinstance(fm_desc, str) or not fm_desc.strip():
        return None, "SKILL.md frontmatter is missing 'description'", warnings
    if len(fm_desc) > 1024:
        warnings.append(f"skill '{dir_slug}': description exceeds 1024 characters")
    # The directory name IS the skill identity (Agent Skills: ``name`` must
    # match the parent directory). A differing frontmatter name is corrected on
    # install; hash the skill as it will be installed.
    original_name: str | None = None
    overrides: dict[str, bytes] | None = None
    if fm_name.strip() != dir_slug:
        original_name = fm_name.strip()
        overrides = {"SKILL.md": rewrite_frontmatter_name(raw, dir_slug).encode("utf-8")}
        warnings.append(
            f"skill '{dir_slug}': frontmatter name '{original_name}' rewritten to '{dir_slug}'"
        )
    meta_version: str | None = None
    meta_raw = metadata.get("metadata")
    if isinstance(meta_raw, dict) and meta_raw.get("version") is not None:
        meta_version = str(meta_raw["version"])
    elif metadata.get("version") is not None:
        # Legacy top-level ``version`` (pre-Agent-Skills convention).
        meta_version = str(metadata["version"])
    return (
        SkillSpec(
            slug=dir_slug,
            path=skill_dir,
            name=dir_slug,
            description=fm_desc.strip(),
            meta_version=meta_version,
            content_hash=hash_directory(skill_dir, ignore_names=ignore_names, overrides=overrides),
            frontmatter=dict(metadata),
            ignore_names=ignore_names,
            original_name=original_name,
        ),
        None,
        warnings,
    )


def discover_skills(
    skills_dir: Path, plugin_root: Path
) -> tuple[list[SkillSpec], list[ComponentFailure], list[str]]:
    """Fixed-location discovery: each IMMEDIATE child of ``skills/`` holding a
    regular ``SKILL.md`` (resolving inside the plugin root) is one skill; no
    recursion; per-skill failures are isolated."""
    skills: list[SkillSpec] = []
    failures: list[ComponentFailure] = []
    warnings: list[str] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        if not is_within(plugin_root, child):
            failures.append(
                ComponentFailure(
                    "skill", child.name, "skill directory resolves outside the plugin root"
                )
            )
            continue
        manifest = child / "SKILL.md"
        if not manifest.is_file():
            continue  # a plain directory, not a skill
        if not is_within(plugin_root, manifest):
            failures.append(
                ComponentFailure("skill", child.name, "SKILL.md resolves outside the plugin root")
            )
            continue
        spec, reason, skill_warnings = read_skill_spec(child)
        warnings.extend(skill_warnings)
        if spec is None:
            failures.append(ComponentFailure("skill", child.name, reason or "invalid skill"))
        else:
            skills.append(spec)
    return skills, failures, warnings


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def detect_plugin_format(root: Path) -> PluginFormat | None:
    if (root / "plugin.json").is_file():
        return "agent_plugins"
    for dirname, fmt in LEGACY_MANIFEST_DIRS.items():
        if (root / dirname / "plugin.json").is_file():
            return fmt
    return None


def find_plugin_root(root: Path) -> Path | None:
    """``root`` itself when it holds a manifest, else its single top-level
    directory when THAT does (a zip of a folder) — otherwise ``None``."""
    if detect_plugin_format(root) is not None:
        return root
    if not root.is_dir():
        return None
    children = [p for p in root.iterdir() if not p.name.startswith("__MACOSX")]
    dirs = [p for p in children if p.is_dir()]
    if len(dirs) == 1 and detect_plugin_format(dirs[0]) is not None:
        return dirs[0]
    return None


def load_plugin_dir(root: Path) -> LoadedPlugin:
    """Load a plugin directory of any supported layout (see module docstring).

    Raises :class:`PluginManifestError` when there is no manifest or the
    manifest is fatally invalid.
    """
    fmt = detect_plugin_format(root)
    if fmt is None:
        raise PluginManifestError(
            "no plugin manifest found (expected plugin.json, .claude-plugin/plugin.json "
            "or .codebuddy-plugin/plugin.json)"
        )
    if fmt == "agent_plugins":
        return load_agent_plugin(root)
    return load_legacy_plugin(root, fmt)


def load_agent_plugin(root: Path) -> LoadedPlugin:
    manifest_path = root / "plugin.json"
    if not is_within(root, manifest_path):
        raise PluginManifestError("plugin.json resolves outside the plugin root")
    try:
        data = _read_json_file(manifest_path)
    except (OSError, ValueError) as exc:
        raise PluginManifestError(f"cannot read plugin.json: {exc}") from exc
    manifest, warnings = parse_plugin_manifest(data)
    loaded = LoadedPlugin(root=root, format="agent_plugins", manifest=manifest, warnings=warnings)

    skills_dir = root / "skills"
    if skills_dir.exists():
        if not skills_dir.is_dir() or not is_within(root, skills_dir):
            loaded.skipped.append(
                ComponentFailure(
                    "skills", "skills", "'skills' is not a directory inside the plugin"
                )
            )
        else:
            skills, failures, skill_warnings = discover_skills(skills_dir, root)
            loaded.skills.extend(skills)
            loaded.skipped.extend(failures)
            loaded.warnings.extend(skill_warnings)

    mcp_path = root / "mcp.json"
    if mcp_path.exists():
        if not mcp_path.is_file() or not is_within(root, mcp_path):
            loaded.skipped.append(
                ComponentFailure(
                    "mcp", "mcp.json", "'mcp.json' is not a regular file inside the plugin"
                )
            )
        else:
            try:
                mcp_data = _read_json_file(mcp_path)
            except (OSError, ValueError) as exc:
                loaded.skipped.append(ComponentFailure("mcp", "mcp.json", str(exc)))
            else:
                servers, failures, top_error = parse_mcp_config(mcp_data)
                if top_error is not None:
                    loaded.skipped.append(ComponentFailure("mcp", "mcp.json", top_error))
                loaded.servers.extend(servers)
                loaded.skipped.extend(failures)
    for failure in loaded.skipped:
        loaded.warnings.append(f"{failure.kind} '{failure.ref}' skipped: {failure.reason}")
    return loaded


# -- legacy (.claude-plugin / .codebuddy-plugin) ------------------------------


def _legacy_name(raw: object, fallback: str) -> tuple[str, list[str]]:
    """Derive a spec-conformant plugin name from a legacy manifest name."""
    warnings: list[str] = []
    candidate = str(raw).strip() if isinstance(raw, str) and raw.strip() else fallback
    if validate_plugin_name(candidate) is None:
        return candidate, warnings
    normalized = re.sub(r"[^a-z0-9.-]+", "-", candidate.lower())
    normalized = re.sub(r"-{2,}", "-", normalized)
    normalized = re.sub(r"\.{2,}", ".", normalized).strip("-.")[:64].strip("-.")
    if validate_plugin_name(normalized) is not None:
        normalized = "plugin"
    warnings.append(f"plugin name {candidate!r} normalized to {normalized!r}")
    return normalized, warnings


def _legacy_manifest(
    data: dict[str, Any], fmt: PluginFormat, root: Path
) -> tuple[PluginManifest, list[str]]:
    warnings: list[str] = []
    name, name_warnings = _legacy_name(data.get("name"), root.name)
    warnings.extend(name_warnings)
    author: PluginAuthorSpec | None = None
    author_raw = data.get("author")
    if isinstance(author_raw, dict):
        author = PluginAuthorSpec(
            name=_opt_str(author_raw.get("name")),
            email=_opt_str(author_raw.get("email")),
            url=_opt_str(author_raw.get("url")),
        )
    elif isinstance(author_raw, str):
        author = PluginAuthorSpec(name=author_raw)
    keywords_raw = data.get("keywords")
    keywords = (
        [str(k) for k in keywords_raw if isinstance(k, str)]
        if isinstance(keywords_raw, list)
        else []
    )
    description = _opt_str(data.get("description"))
    if not description:
        description = _opt_str(data.get("description_en")) or _opt_str(data.get("description_zh"))
    ignored = sorted(
        k
        for k in ("agents", "commands", "hooks", "rules", "lspServers", "expertType", "agentName")
        if k in data
    )
    if ignored:
        warnings.append(
            f"legacy manifest field(s) not supported by Agent Plugins ignored: {', '.join(ignored)}"
        )
    # Keep the original manifest verbatim under the Valuz extension namespace
    # so nothing authored upstream is lost (agents / experts / display data).
    extensions: dict[str, dict[str, Any]] = {
        VALUZ_EXTENSION_NS: {"legacy_format": fmt, "legacy_manifest": json.loads(json.dumps(data))}
    }
    manifest = PluginManifest(
        name=name,
        version=_opt_str(data.get("version")) or None,
        description=description or None,
        author=author,
        homepage=_opt_str(data.get("homepage")) or None,
        repository=_opt_str(data.get("repository")) or None,
        license=_opt_str(data.get("license")) or None,
        keywords=keywords,
        extensions=extensions,
    )
    return manifest, warnings


def _legacy_skill_paths(data: dict[str, Any], root: Path) -> tuple[list[Path], list[str]]:
    """Manifest-declared skill locations: a string or list of ``./``-relative
    paths, each either a skill directory or a directory OF skills."""
    warnings: list[str] = []
    raw = data.get("skills")
    entries: list[str]
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, str)]
    else:
        entries = []
    dirs: list[Path] = []
    for entry in entries:
        rel = entry[2:] if entry.startswith("./") else entry
        target = root / rel
        if not target.is_dir() or not is_within(root, target):
            warnings.append(f"declared skill path '{entry}' is not a directory inside the plugin")
            continue
        if (target / "SKILL.md").is_file():
            dirs.append(target)
        else:
            dirs.extend(sorted(p for p in target.iterdir() if p.is_dir()))
    return dirs, warnings


def convert_legacy_mcp_server(
    name: str, entry: object
) -> tuple[McpServerSpec | None, str | None, list[str]]:
    """Map a Claude-format server entry into an Agent Plugins variant.

    Claude / WorkBuddy shapes: ``{command, args, env}`` (implicit stdio),
    ``{type: "http" | "sse" | "stdio", url, headers}``; ``${CLAUDE_PLUGIN_ROOT}``
    / ``${CODEBUDDY_PLUGIN_ROOT}`` become ``${PLUGIN_ROOT}``; unsupported
    fields (``timeout`` / ``disabled`` / ``defer_loading`` …) are dropped with
    a warning; header values that reference environment variables are dropped
    (Agent Plugins forbids header expansion — the user wires credentials
    through the connector instead).
    """
    warnings: list[str] = []
    if not isinstance(entry, dict):
        return None, "server entry must be an object", warnings
    raw_type = entry.get("type") or entry.get("transportType") or entry.get("transport")
    if raw_type is None:
        raw_type = "stdio" if "command" in entry else "streamable-http" if "url" in entry else None
    type_map = {
        "stdio": "stdio",
        "http": "streamable-http",
        "streamable-http": "streamable-http",
        "streamable_http": "streamable-http",
        "streamableHttp": "streamable-http",
        "sse": "sse",
    }
    mapped = type_map.get(str(raw_type)) if raw_type is not None else None
    if mapped is None:
        return None, f"unsupported transport {raw_type!r}", warnings

    def _root_placeholder(value: str) -> str:
        return _LEGACY_ROOT_RE.sub("${PLUGIN_ROOT}", value)

    normalized: dict[str, Any] = {"type": mapped}
    if mapped == "stdio":
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            return None, "missing 'command'", warnings
        command = _root_placeholder(command)
        if command.startswith("${PLUGIN_ROOT}/"):
            command = "./" + command[len("${PLUGIN_ROOT}/") :]
        normalized["command"] = command
        args = entry.get("args")
        if isinstance(args, list):
            normalized["args"] = [_root_placeholder(str(a)) for a in args]
        env = entry.get("env")
        if isinstance(env, dict):
            normalized["env"] = {
                str(k): _root_placeholder(str(v)) for k, v in env.items() if k not in _RESERVED_ENV
            }
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd:
            cwd = _root_placeholder(cwd)
            if cwd.startswith("${PLUGIN_ROOT}") or cwd.startswith("./"):
                normalized["cwd"] = cwd
            else:
                warnings.append(f"server '{name}': unsupported cwd {cwd!r} dropped")
        known = {"type", "transportType", "transport", "command", "args", "env", "cwd"}
    else:
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            return None, "missing 'url'", warnings
        normalized["url"] = url
        headers = entry.get("headers")
        if isinstance(headers, dict):
            kept: dict[str, str] = {}
            for k, v in headers.items():
                if not isinstance(v, str):
                    continue
                if _LEGACY_ENV_REF_RE.search(v):
                    warnings.append(
                        f"server '{name}': header '{k}' references an environment variable and was "
                        "dropped — configure it on the connector instead"
                    )
                    continue
                kept[str(k)] = v
            if kept:
                normalized["headers"] = kept
        known = {"type", "transportType", "transport", "url", "headers"}
    dropped = sorted(set(entry) - known)
    if dropped:
        warnings.append(f"server '{name}': unsupported field(s) dropped: {', '.join(dropped)}")
    spec, reason = parse_mcp_server(name, normalized)
    return spec, reason, warnings


def _legacy_servers_object(
    data: dict[str, Any], root: Path
) -> tuple[dict[str, Any] | None, list[str]]:
    """Locate the legacy MCP servers object: inline ``mcpServers`` (object or a
    path string), else ``.mcp.json`` (``{mcpServers: {...}}`` or bare)."""
    warnings: list[str] = []
    inline = data.get("mcpServers")
    candidates: list[Path] = []
    if isinstance(inline, dict):
        return inline, warnings
    if isinstance(inline, str):
        rel = inline[2:] if inline.startswith("./") else inline
        candidates.append(root / rel)
    candidates.append(root / ".mcp.json")
    for path in candidates:
        if not path.is_file() or not is_within(root, path):
            continue
        try:
            payload = _read_json_file(path)
        except (OSError, ValueError) as exc:
            warnings.append(f"cannot read {path.name}: {exc}")
            continue
        if not isinstance(payload, dict):
            warnings.append(f"{path.name} must contain a JSON object")
            continue
        wrapped = payload.get("mcpServers")
        if isinstance(wrapped, dict):
            return wrapped, warnings
        return payload, warnings
    return None, warnings


def load_legacy_plugin(root: Path, fmt: PluginFormat) -> LoadedPlugin:
    manifest_dir = next(d for d, f in LEGACY_MANIFEST_DIRS.items() if f == fmt)
    manifest_path = root / manifest_dir / "plugin.json"
    try:
        data = _read_json_file(manifest_path)
    except (OSError, ValueError) as exc:
        raise PluginManifestError(f"cannot read {manifest_dir}/plugin.json: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginManifestError(f"{manifest_dir}/plugin.json must contain a JSON object")
    manifest, warnings = _legacy_manifest(data, fmt, root)
    loaded = LoadedPlugin(root=root, format=fmt, manifest=manifest, warnings=warnings)

    # Skills: fixed ``skills/`` + manifest-declared paths + a root SKILL.md.
    seen: set[Path] = set()
    skill_dirs: list[Path] = []
    skills_dir = root / "skills"
    if skills_dir.is_dir() and is_within(root, skills_dir):
        skill_dirs.extend(sorted(p for p in skills_dir.iterdir() if p.is_dir()))
    declared, decl_warnings = _legacy_skill_paths(data, root)
    loaded.warnings.extend(decl_warnings)
    skill_dirs.extend(declared)
    for skill_dir in skill_dirs:
        try:
            key = skill_dir.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        if not (skill_dir / "SKILL.md").is_file():
            continue
        if not is_within(root, skill_dir) or not is_within(root, skill_dir / "SKILL.md"):
            loaded.skipped.append(
                ComponentFailure("skill", skill_dir.name, "skill resolves outside the plugin root")
            )
            continue
        spec, reason, skill_warnings = read_skill_spec(skill_dir)
        loaded.warnings.extend(skill_warnings)
        if spec is None:
            loaded.skipped.append(
                ComponentFailure("skill", skill_dir.name, reason or "invalid skill")
            )
        elif any(existing.slug == spec.slug for existing in loaded.skills):
            loaded.skipped.append(
                ComponentFailure(
                    "skill", str(skill_dir.relative_to(root)), f"duplicate skill slug '{spec.slug}'"
                )
            )
        else:
            loaded.skills.append(spec)
    if (root / "SKILL.md").is_file() and not any(s.slug == manifest.name for s in loaded.skills):
        spec, reason, skill_warnings = read_skill_spec(
            root, slug=manifest.name, ignore_names=_LEGACY_ROOT_SKILL_IGNORE
        )
        loaded.warnings.extend(skill_warnings)
        if spec is None:
            loaded.skipped.append(
                ComponentFailure("skill", manifest.name, reason or "invalid skill")
            )
        else:
            loaded.skills.append(spec)

    # MCP servers.
    servers_obj, mcp_warnings = _legacy_servers_object(data, root)
    loaded.warnings.extend(mcp_warnings)
    if servers_obj is not None:
        for name, entry in servers_obj.items():
            server, reason, server_warnings = convert_legacy_mcp_server(str(name), entry)
            loaded.warnings.extend(server_warnings)
            if server is None:
                loaded.skipped.append(ComponentFailure("connector", str(name), reason or "invalid"))
            else:
                loaded.servers.append(server)

    # Unsupported legacy component directories → warning only.
    ignored_dirs = sorted(
        d for d in ("agents", "commands", "hooks", "rules") if (root / d).is_dir()
    )
    if ignored_dirs:
        loaded.warnings.append(
            "legacy component directories not supported by Agent Plugins ignored: "
            + ", ".join(ignored_dirs)
        )
    for failure in loaded.skipped:
        loaded.warnings.append(f"{failure.kind} '{failure.ref}' skipped: {failure.reason}")
    return loaded


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------

MAX_ARCHIVE_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB per file
MAX_ARCHIVE_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MiB per plugin
MAX_ARCHIVE_FILE_COUNT = 4096
_DRIVE_RE = re.compile(r"[A-Za-z]:")


class PluginArchiveError(ValueError):
    """Malformed / oversized / unsafe plugin zip."""


def _safe_member_path(name: str) -> list[str] | None:
    posix = name.replace("\\", "/")
    parts: list[str] = []
    for seg in posix.split("/"):
        if not seg or seg == ".":
            continue
        if seg == ".." or _DRIVE_RE.fullmatch(seg):
            return None
        parts.append(seg)
    return parts


def extract_plugin_zip(data: bytes, dest: Path) -> Path:
    """Extract a plugin zip into ``dest`` (created) with caps + zip-slip
    guards, and return the plugin root inside it (a single wrapping folder is
    tolerated). Symlink entries are written as regular files (never followed).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PluginArchiveError("not a valid zip archive") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ARCHIVE_FILE_COUNT:
        raise PluginArchiveError(f"archive exceeds the {MAX_ARCHIVE_FILE_COUNT}-file limit")
    total = 0
    for info in infos:
        if info.file_size > MAX_ARCHIVE_FILE_BYTES:
            raise PluginArchiveError(f"file {info.filename!r} exceeds the per-file size limit")
        total += info.file_size
        if total > MAX_ARCHIVE_TOTAL_BYTES:
            raise PluginArchiveError("archive exceeds the total size limit")
    dest.mkdir(parents=True, exist_ok=True)
    for info in infos:
        parts = _safe_member_path(info.filename)
        if parts is None:
            raise PluginArchiveError(f"unsafe path in archive: {info.filename!r}")
        if not parts:
            continue
        target = dest.joinpath(*parts)
        if not is_within(dest, target):
            raise PluginArchiveError(f"unsafe path in archive: {info.filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
    root = find_plugin_root(dest)
    if root is None:
        raise PluginManifestError(
            "archive does not contain a plugin (no plugin.json, .claude-plugin/plugin.json "
            "or .codebuddy-plugin/plugin.json at its root)"
        )
    return root


def build_export_zip(
    manifest: PluginManifest,
    skill_dirs: dict[str, Path],
    mcp_config: dict[str, Any] | None,
    *,
    extension_files: dict[str, bytes] | None = None,
) -> bytes:
    """Write an Agent Plugins layout zip: root ``plugin.json``, ``skills/<slug>/``
    (from the given on-disk directories), ``mcp.json`` when there are servers,
    and any Valuz-specific files under ``io.valuz.agent/`` (``extension_files``
    keys are paths relative to that directory)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "plugin.json", json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n"
        )
        for slug, src in skill_dirs.items():
            if not src.is_dir():
                continue
            for path in _iter_contained_files(src):
                rel = path.relative_to(src).as_posix()
                if rel == "SKILL.md":
                    # Spec-conformant export: the frontmatter name IS the slug.
                    zf.writestr(
                        f"skills/{slug}/SKILL.md",
                        rewrite_frontmatter_name(_read_text(path), slug).encode("utf-8"),
                    )
                    continue
                zf.write(path, f"skills/{slug}/{rel}")
        if mcp_config:
            zf.writestr("mcp.json", json.dumps(mcp_config, indent=2, ensure_ascii=False) + "\n")
        for rel, payload in (extension_files or {}).items():
            zf.writestr(f"{VALUZ_EXTENSION_NS}/{rel.lstrip('/')}", payload)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Materialization (what lands in PLUGIN_ROOT) + straight zip of a root
# ---------------------------------------------------------------------------

# Legacy top-level entries that are client-specific in Agent Plugins terms —
# preserved under ``io.valuz.agent/legacy/`` so nothing authored upstream is lost.
LEGACY_FORMAT_ENTRIES = frozenset(
    {".claude-plugin", ".codebuddy-plugin", ".mcp.json", "agents", "commands", "hooks", "rules"}
)
_MATERIALIZE_SKIP = frozenset({".git", "node_modules"})


def _copy_entry(src: Path, dst: Path) -> list[str]:
    if src.is_dir():
        return copy_tree_contained(src, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return []


def materialize_plugin(loaded: LoadedPlugin, dest: Path) -> list[str]:
    """Write ``loaded`` into ``dest`` in the Agent Plugins layout — this is
    what becomes ``PLUGIN_ROOT``, so on-disk plugins are uniform whatever
    layout they were read from.

    * ``agent_plugins`` — the package is copied verbatim (contained copy) and
      every discovered skill's SKILL.md ``name`` is corrected to its directory
      name.
    * legacy (``claude_plugin`` / ``codebuddy_plugin``) — root ``plugin.json``
      (closed fields; the original manifest under ``extensions["io.valuz.agent"]``),
      ``skills/<slug>/`` for every discovered skill (a root ``SKILL.md`` becomes
      ``skills/<plugin-name>/`` with the skill's own files), ``mcp.json`` when
      servers exist, ``io.valuz.agent/legacy/`` for agents / commands / hooks /
      rules / ``.mcp.json`` / the legacy manifest dir, and the remaining
      top-level entries (README, LICENSE, ``bin/`` …) verbatim so
      plugin-relative MCP paths keep resolving. Returns warnings.
    """
    warnings: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    root = loaded.root
    if loaded.format == "agent_plugins":
        warnings.extend(copy_tree_contained(root, dest))
        for skill in loaded.skills:
            try:
                rel = skill.path.relative_to(root)
            except ValueError:
                continue
            original = normalize_skill_dir(dest / rel, skill.slug)
            if original is not None:
                warnings.append(
                    f"skill '{skill.slug}': frontmatter name '{original}' rewritten to "
                    f"'{skill.slug}'"
                )
        return warnings

    (dest / "plugin.json").write_text(
        json.dumps(loaded.manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    skill_sources: set[Path] = set()
    root_skill = False
    for skill in loaded.skills:
        try:
            resolved = skill.path.resolve()
        except OSError:
            resolved = skill.path
        skill_sources.add(resolved)
        if resolved == root.resolve():
            root_skill = True
        target = dest / "skills" / skill.slug
        warnings.extend(copy_tree_contained(skill.path, target, ignore_names=skill.ignore_names))
        original = normalize_skill_dir(target, skill.slug)
        if original is not None:
            warnings.append(
                f"skill '{skill.slug}': frontmatter name '{original}' rewritten to '{skill.slug}'"
            )
    mcp = loaded.mcp_config()
    if mcp:
        (dest / "mcp.json").write_text(
            json.dumps(mcp, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    legacy_dest = dest / VALUZ_EXTENSION_NS / "legacy"
    # A root-skill plugin's files ARE the skill — they move under skills/<name>/;
    # keep root copies only when MCP servers may reference plugin-relative paths.
    keep_root_files = not root_skill or bool(loaded.servers)
    for entry in sorted(root.iterdir()):
        name = entry.name
        if name in LEGACY_FORMAT_ENTRIES:
            warnings.extend(_copy_entry(entry, legacy_dest / name))
            continue
        if name == "skills" or name in _MATERIALIZE_SKIP or name in _JUNK_NAMES:
            continue
        if root_skill and name == "SKILL.md":
            continue
        try:
            resolved = entry.resolve()
        except OSError:
            continue
        if entry.is_dir() and any(
            src == resolved or _is_relative(src, resolved) for src in skill_sources
        ):
            continue  # a declared skill dir (or a dir of them) — already under skills/
        if not keep_root_files:
            continue
        warnings.extend(_copy_entry(entry, dest / name))
    return warnings


def _is_relative(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def zip_plugin_root(root: Path) -> bytes:
    """Straight zip of an installed plugin root (contained files only); the
    ``skills/<slug>/SKILL.md`` names are re-asserted to their slugs so the
    export is spec-conformant even after a local edit."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_contained_files(root):
            rel = path.relative_to(root)
            parts = rel.parts
            if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
                zf.writestr(
                    rel.as_posix(),
                    rewrite_frontmatter_name(_read_text(path), parts[1]).encode("utf-8"),
                )
                continue
            zf.write(path, rel.as_posix())
    return buffer.getvalue()


__all__ = [
    "LEGACY_FORMAT_ENTRIES",
    "LEGACY_MANIFEST_DIRS",
    "MAX_ARCHIVE_FILE_BYTES",
    "MAX_ARCHIVE_FILE_COUNT",
    "MAX_ARCHIVE_TOTAL_BYTES",
    "MCP_SCHEMA_ID",
    "PLUGIN_SCHEMA_ID",
    "SPEC_VERSION",
    "VALUZ_EXTENSION_NS",
    "ComponentFailure",
    "LoadedPlugin",
    "McpServerSpec",
    "McpTransport",
    "PluginArchiveError",
    "PluginAuthorSpec",
    "PluginFormat",
    "PluginManifest",
    "PluginManifestError",
    "ResolvedLaunch",
    "SkillSpec",
    "build_export_zip",
    "convert_legacy_mcp_server",
    "copy_tree_contained",
    "detect_plugin_format",
    "discover_skills",
    "expand_placeholders",
    "extract_plugin_zip",
    "find_plugin_root",
    "hash_directory",
    "hash_json",
    "is_within",
    "load_agent_plugin",
    "load_legacy_plugin",
    "load_plugin_dir",
    "materialize_plugin",
    "normalize_skill_dir",
    "parse_mcp_config",
    "parse_mcp_server",
    "parse_plugin_manifest",
    "read_skill_spec",
    "resolve_stdio_launch",
    "rewrite_frontmatter_name",
    "validate_plugin_name",
    "validate_skill_name",
    "zip_plugin_root",
]
