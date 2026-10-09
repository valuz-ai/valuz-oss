"""Command wrappers every agent session can call by name.

Three bundled tools, installed at boot as small wrappers in the session bin dir
(``FsRegistry.session_bin_dir``) and prepended to this process's PATH, so every
runtime subprocess spawned afterwards — Claude, Codex, DeepAgents, DeepSeek
Harness — resolves them the same way (the ``chrome-devtools`` wrapper pattern):

* ``valuz-python`` — the bundled session Python
  (``backend/vendor/python-runtime``: CPython + the libraries the bundled skills
  import). Packaged desktop: the sidecar points ``VALUZ_PYTHON_RUNTIME`` at the
  staged ``libexec/python-runtime``; a dev checkout uses the vendored ``dist/``
  (``scripts/vendor-python-runtime.sh``).
* ``dsoffice`` — the LibreOffice Kit CLI (``@deepseek-ai/libreoffice-kit``:
  convert, render to PNG, recalculate) shipped inside the DeepSeek Harness
  runtime closure, run on Node. Packaged desktop: the closure the sidecar names
  through ``VALUZ_DSH_RUNTIME_ENTRY``, run under the app's Electron binary as
  Node; a dev checkout uses ``backend/vendor/dsh-runtime``.

* ``valuz-plugin`` — the plugin SDK CLI (``@valuz/plugin-sdk``'s ``bin/valuz-plugin.mjs``:
  ``create`` / ``build`` / ``test`` / ``validate`` / ``pack``), run on Node, so an agent
  can build a third-party Valuz plugin in a session (docs plugin-development/05).
  Packaged desktop: the self-contained distribution
  (``scripts/build-plugin-sdk-dist.mjs``: the CLI, the SDK runtime pre-bundled into
  ``runtime/*.mjs``, the pinned esbuild + React) staged at ``libexec/plugin-sdk``;
  the sidecar points ``VALUZ_PLUGIN_SDK_ENTRY`` at its ``bin/valuz-plugin.mjs``,
  run under the app's Electron as Node. A source checkout uses the package's own
  ``bin`` (the SDK from its TypeScript sources).

Cloud sandboxes do not go through this module: the kernel image ships the same
commands in ``/usr/local/bin``.

A missing tool is skipped (logged at INFO), never raised: the skills that use
these commands report the missing tool instead of failing a boot.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

PYTHON_COMMAND = "valuz-python"
OFFICE_COMMAND = "dsoffice"
PLUGIN_COMMAND = "valuz-plugin"

#: Root of the staged python runtime (packaged desktop; set by the sidecar).
PYTHON_RUNTIME_ENV = "VALUZ_PYTHON_RUNTIME"
#: The dsh closure's launcher (packaged desktop; set by the sidecar).
DSH_RUNTIME_ENTRY_ENV = "VALUZ_DSH_RUNTIME_ENTRY"
#: The plugin SDK CLI entry (packaged desktop; set by the sidecar).
PLUGIN_SDK_ENTRY_ENV = "VALUZ_PLUGIN_SDK_ENTRY"
NODE_PATH_ENV = "VALUZ_NODE_PATH"
NODE_IS_ELECTRON_ENV = "VALUZ_NODE_IS_ELECTRON"

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_VENDORED_PYTHON_RUNTIME = _BACKEND_DIR / "vendor" / "python-runtime" / "dist"
_VENDORED_DSH_NODE_MODULES = _BACKEND_DIR / "vendor" / "dsh-runtime" / "node_modules"
_KIT_CLI_REL = Path("@deepseek-ai") / "libreoffice-kit" / "lib" / "cli.js"
# <repo>/backend -> <repo>/frontend/packages/plugin-sdk/bin/valuz-plugin.mjs
_SOURCE_PLUGIN_SDK_ENTRY = (
    _BACKEND_DIR.parent / "frontend" / "packages" / "plugin-sdk" / "bin" / "valuz-plugin.mjs"
)


def _is_windows() -> bool:
    return os.name == "nt"


def python_executable() -> Path | None:
    """The bundled session interpreter, or ``None`` when none is installed."""
    configured = os.environ.get(PYTHON_RUNTIME_ENV, "").strip()
    root = Path(configured) if configured else _VENDORED_PYTHON_RUNTIME
    for candidate in (root / "bin" / "python3", root / "python.exe"):
        if candidate.is_file():
            return candidate
    return None


def office_kit_cli() -> Path | None:
    """The LibreOffice Kit CLI entry inside the dsh closure, or ``None``."""
    entry = os.environ.get(DSH_RUNTIME_ENTRY_ENV, "").strip()
    # <closure>/node_modules/valuz-dsh-bundle/bin/dsh.mjs → <closure>/node_modules
    node_modules = Path(entry).parents[2] if entry else _VENDORED_DSH_NODE_MODULES
    cli = node_modules / _KIT_CLI_REL
    return cli if cli.is_file() else None


def plugin_sdk_cli() -> Path | None:
    """The plugin SDK CLI entry, or ``None`` when none is installed.

    ``VALUZ_PLUGIN_SDK_ENTRY`` wins (packaged desktop:
    ``libexec/plugin-sdk/bin/valuz-plugin.mjs``); a source checkout falls back to
    the package's own ``bin``.
    """
    configured = os.environ.get(PLUGIN_SDK_ENTRY_ENV, "").strip()
    for candidate in (Path(configured) if configured else None, _SOURCE_PLUGIN_SDK_ENTRY):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _node() -> tuple[str, bool] | None:
    """The Node carrier and whether it is Electron run as Node."""
    configured = os.environ.get(NODE_PATH_ENV, "").strip()
    if configured:
        return configured, os.environ.get(NODE_IS_ELECTRON_ENV) == "1"
    found = shutil.which("node")
    return (found, False) if found else None


def _wrapper_path(bin_dir: Path, name: str) -> Path:
    return bin_dir / (f"{name}.cmd" if _is_windows() else name)


def wrapper_body(argv: list[str], *, electron_as_node: bool = False) -> str:
    """A wrapper forwarding its arguments to ``argv``.

    Electron-as-node needs ``ELECTRON_RUN_AS_NODE=1`` in the wrapper itself:
    agent shells run the wrapper directly, so no spawn-time env can add it.
    """
    if _is_windows():
        quoted = " ".join(f'"{a}"' for a in argv)
        electron = 'set "ELECTRON_RUN_AS_NODE=1"\r\n' if electron_as_node else ""
        return f"@echo off\r\n{electron}{quoted} %*\r\n"
    quoted = " ".join(shlex.quote(a) for a in argv)
    electron = "export ELECTRON_RUN_AS_NODE=1\n" if electron_as_node else ""
    return f'#!/bin/sh\n{electron}exec {quoted} "$@"\n'


def _write_wrapper(bin_dir: Path, name: str, body: str) -> None:
    wrapper = _wrapper_path(bin_dir, name)
    if not wrapper.is_file() or wrapper.read_text(encoding="utf-8") != body:
        wrapper.write_text(body, encoding="utf-8")
    if not _is_windows():
        wrapper.chmod(0o755)


def _remove_wrapper(bin_dir: Path, name: str) -> None:
    # A wrapper left from an earlier install would point at a tool that is gone.
    _wrapper_path(bin_dir, name).unlink(missing_ok=True)


def _prepend_path(directory: str) -> None:
    sep = os.pathsep
    parts = [p for p in os.environ.get("PATH", "").split(sep) if p and p != directory]
    os.environ["PATH"] = sep.join([directory, *parts])


def ensure_session_tools_on_path(bin_dir: Path) -> list[str]:
    """Install the available wrappers into ``bin_dir`` and put it first on PATH.

    Must run at boot, before any session spawns its agent subprocess (PATH is
    inherited at spawn time). Idempotent. Returns the installed command names.
    """
    installed: list[str] = []

    python = python_executable()
    if python is not None:
        _write_wrapper(bin_dir, PYTHON_COMMAND, wrapper_body([str(python)]))
        installed.append(PYTHON_COMMAND)
    else:
        _remove_wrapper(bin_dir, PYTHON_COMMAND)
        logger.info("bundled session python not installed; %s unavailable", PYTHON_COMMAND)

    cli = office_kit_cli()
    node = _node()
    if cli is not None and node is not None:
        node_bin, is_electron = node
        body = wrapper_body([node_bin, str(cli)], electron_as_node=is_electron)
        _write_wrapper(bin_dir, OFFICE_COMMAND, body)
        installed.append(OFFICE_COMMAND)
    else:
        _remove_wrapper(bin_dir, OFFICE_COMMAND)
        logger.info("LibreOffice Kit or Node not found; %s unavailable", OFFICE_COMMAND)

    sdk = plugin_sdk_cli()
    if sdk is not None and node is not None:
        node_bin, is_electron = node
        body = wrapper_body([node_bin, str(sdk)], electron_as_node=is_electron)
        _write_wrapper(bin_dir, PLUGIN_COMMAND, body)
        installed.append(PLUGIN_COMMAND)
    else:
        _remove_wrapper(bin_dir, PLUGIN_COMMAND)
        logger.info("plugin SDK CLI or Node not found; %s unavailable", PLUGIN_COMMAND)

    _prepend_path(str(bin_dir))
    return installed
