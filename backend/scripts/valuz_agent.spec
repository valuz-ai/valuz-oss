# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Valuz backend server.

Produces a ``--onedir`` bundle under ``dist/valuz-server/`` containing
the standalone ``valuz-server`` executable plus the ``_internal/`` runtime
tree. ``scripts/build-desktop.sh`` then stages the CONTENTS of that dir
directly under ``Valuz.app/Contents/Resources/libexec/`` — flat layout,
no wrapper. See docs/STRUCTURE.md §"Build Artifact Names" / §"Desktop
Distribution".

Strategy
--------
``valuz_agent`` and ``kernel`` are copied as **data directories** (raw .py
files) so that kernel's ``sys.path`` injection and Alembic file reads work
correctly.  Third-party dependencies are declared in ``hiddenimports`` so
PyInstaller includes them in the PYZ archive.

The entry-point script adds ``_internal/`` to ``sys.path[0]`` so Python
finds the data-directory copies of ``valuz_agent`` first.

Usage::

    cd backend
    uv run pyinstaller scripts/valuz_agent.spec --clean --noconfirm
    ./dist/valuz-server/valuz-server --host 127.0.0.1 --port 19100
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# --- Platform binary name ---
if sys.platform == "win32":
    exe_name = "valuz-server.exe"
else:
    exe_name = "valuz-server"

# --- Source root (backend/) ---
# This spec lives in ``backend/scripts/``, so ``SPECPATH`` is that dir; the
# backend root (which holds ``valuz_agent`` / ``kernel`` / ``alembic``) is its
# parent. PyInstaller is still invoked from ``backend/`` (``pyinstaller
# scripts/valuz_agent.spec``), so build/dist land under ``backend/`` as before.
HERE = Path(SPECPATH).parent

# --- Auto-collect all submodules for third-party packages ---
_third_party_pkgs = [
    "fastapi", "starlette", "pydantic", "pydantic_settings",
    "uvicorn", "sse_starlette", "multipart",
    "sqlalchemy", "aiosqlite", "alembic",
    "httpx",
    "markitdown", "pymupdf4llm", "pymupdf",
    "deepagents", "claude_agent_sdk",
    "codex_cli_bin", "openai_codex",
    "langchain_openai", "langchain_mcp_adapters",
    "mcp",
    "croniter", "cron_descriptor", "pytz",
    "jinja2",
    "dotenv", "typer", "watchfiles",
]
_auto_hidden = []
for _pkg in _third_party_pkgs:
    try:
        _auto_hidden.extend(collect_submodules(_pkg))
    except Exception:
        _auto_hidden.append(_pkg)

# Collect non-Python data files that collect_submodules misses.
# PyInstaller's modulegraph handles .py and Python C extensions (.so),
# but standalone binaries, shared libs, models, and locale catalogs
# must be collected explicitly.
_data_pkgs = [
    "claude_agent_sdk",   # _bundled/claude CLI binary (~213MB)
    "codex_cli_bin",      # bin/codex CLI binary (~75MB)
    "pymupdf",            # libmupdf.dylib + ONNX layout models (~51MB)
    "cron_descriptor",    # locale/*.mo translation catalogs
]
_extra_datas = []
for _dpkg in _data_pkgs:
    try:
        _extra_datas.extend(collect_data_files(_dpkg, include_py_files=False))
    except Exception:
        pass

# --- Create a thin entry-point script ---
_entry_script = str(HERE / "_pyinstaller_entry.py")
entry_content = """\
import os, sys

# In the frozen bundle, valuz_agent/ and kernel/ live as real directories
# under _internal/ (placed there via the ``datas`` directive).  We add
# _internal/ to sys.path so standard imports find them.
if getattr(sys, 'frozen', False):
    _internal = os.path.join(os.path.dirname(sys.executable), '_internal')
    if _internal not in sys.path:
        sys.path.insert(0, _internal)

    # Ensure the bundled Claude CLI (from claude_agent_sdk/_bundled/) is
    # discoverable by the SDK's subprocess transport.  The SDK's own
    # ``_find_bundled_cli`` uses ``Path(__file__)`` which may not resolve
    # correctly inside the PYZ archive — adding the directory to PATH lets
    # ``shutil.which("claude")`` find it as a reliable fallback.
    _bundled_dir = os.path.join(_internal, 'claude_agent_sdk', '_bundled')
    if os.path.isfile(os.path.join(_bundled_dir, 'claude')):
        os.environ['PATH'] = _bundled_dir + os.pathsep + os.environ.get('PATH', '')

from valuz_agent.__main__ import main
sys.exit(main())
"""
Path(_entry_script).write_text(entry_content, encoding="utf-8")

a = Analysis(
    [_entry_script],
    pathex=[str(HERE)],
    binaries=[],
    datas=[
        # Data files from third-party packages (bundled binaries, models, locales)
        *_extra_datas,
        # valuz_agent package — raw .py files, loaded via sys.path
        (str(HERE / "valuz_agent"), "valuz_agent"),
        # Vendored kernel — same strategy; kernel/__init__.py injects its
        # own path for bare imports (src.*, app.*).
        (str(HERE / "kernel"), "kernel"),
        # Alembic chains, moved out of the package trees to backend/alembic/
        # {host,kernel}; boot resolves them relative to backend/ (= _internal/).
        (str(HERE / "alembic"), "alembic"),
        # Shared i18n locale catalogs (repo-root i18n/locales/, one level above
        # backend/). The backend's t() loads these at runtime; without bundling
        # them, any server-side t() in the packaged app raised "Cannot locate
        # repo root" (i18n._locales_dir reads them from _internal/i18n/locales).
        (str(HERE.parent / "i18n" / "locales"), "i18n/locales"),
    ],
    hiddenimports=[
        # Third-party dependencies — all submodules auto-collected
        *_auto_hidden,
        # OCR (optional, may not be installed)
        "rapidocr_onnxruntime",
    ],
    hookspath=[str(Path(SPECPATH) / "pyinstaller_hooks")],  # sits next to this spec (backend/scripts/)
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "numpy.testing",
        "pytest",
        "mypy",
        "ruff",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    name="valuz-server",
)
