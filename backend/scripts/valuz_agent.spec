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

# Never strip on Windows: PyInstaller shells out to whatever ``strip`` is on
# PATH, and the GitHub windows runners expose GNU binutils strip (Strawberry
# Perl / mingw64), which corrupts MSVC-built PE DLLs — the packaged app then
# dies at launch with "[PYI-xxxxx:ERROR] Failed to load Python DLL …
# LoadLibrary". Strip has no size benefit on Windows anyway.
strip_binaries = sys.platform != "win32"

# --- Source root (backend/) ---
# This spec lives in ``backend/scripts/``, so ``SPECPATH`` is that dir; the
# backend root (which holds ``valuz_agent`` / ``kernel`` / ``alembic``) is its
# parent. PyInstaller is still invoked from ``backend/`` (``pyinstaller
# scripts/valuz_agent.spec``), so build/dist land under ``backend/`` as before.
HERE = Path(SPECPATH).parent

# --- Auto-collect all submodules for third-party packages ---
# Pip package name → Python import name mapping.
# collect_submodules handles the rest; if a package is not found, it falls
# back to adding the bare name as a hidden import.
_third_party_pkgs = [
    # Web framework
    "fastapi", "starlette", "pydantic", "pydantic_settings",
    "uvicorn", "sse_starlette", "multipart",
    # Database
    "sqlalchemy", "aiosqlite", "alembic",
    # HTTP
    "httpx", "httpcore", "httpx_sse", "h11", "httptools",
    # LLM providers
    "anthropic", "openai",
    # Document parsing
    "anydoc", "pymupdf4llm", "pymupdf",
    "html_to_markdown", "rapidocr",
    "markdown_it",
    # ``bs4`` is the module name; the distribution is "beautifulsoup4". Only a
    # module name means anything to PyInstaller, so the old spelling collected
    # nothing. It mattered little while markitdown imported bs4 anyway — with
    # markitdown gone, html-to-markdown is its only requester.
    "bs4",
    "filetype",
    # OCR / ML
    "onnxruntime", "flatbuffers", "numpy",
    "opencv-python",
    # Agent runtimes
    "deepagents", "claude_agent_sdk",
    "codex_cli_bin", "openai_codex",
    # Token counting for the goal-mode length fence (agent_resolver). The
    # ``tiktoken_ext`` namespace package holds the encoding constructors
    # (o200k_base lives in ``tiktoken_ext.openai_public``); collect_submodules
    # of the bare ``tiktoken`` package misses it, so it is listed explicitly —
    # without it ``get_encoding`` raises and counting silently degrades to the
    # char heuristic. The vocab blob itself is vendored (see the
    # ``vendor/tiktoken`` data dir below); tiktoken ships no vocab data files.
    "tiktoken", "tiktoken_ext", "tiktoken_ext.openai_public",
    # LangChain ecosystem
    "langchain", "langchain_core", "langchain_protocol",
    "langchain_openai", "langchain_mcp_adapters",
    "langchain_anthropic", "langchain_google_genai",
    "langgraph",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.prebuilt",
    "langgraph_sdk", "langsmith",
    # Google
    "google.auth", "google.genai",
    # MCP
    "mcp",
    # Scheduling
    "croniter", "cron_descriptor", "pytz",
    # Data
    "orjson", "ormsgpack", "packaging",
    # Crypto / security
    "cryptography", "cffi",
    # Templating
    "jinja2", "mako", "markupsafe",
    # Config / env
    "dotenv", "omegaconf",
    # CLI / logging
    "typer", "click", "colorlog", "watchfiles",
    # Serialization
    "jsonpatch", "jsonpointer", "jsonschema",
    # Networking
    "anyio", "certifi", "charset_normalizer", "idna",
    # Async
    "greenlet",
    # Misc
    "yaml", "networkx", "annotated_types",
    "distro", "docstring_parser", "nh3",
    "colorama", "jiter",
    "attr", "pathspec", "iniconfig",
    "pathspec",
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
    # NOTE: magika used to live here — MarkItDown constructed it eagerly and its
    # ONNX model ships as package data, so a missed entry broke ALL office
    # parsing in the frozen build (PR #231). Both it and MarkItDown are gone:
    # anydoc is a compiled extension with no data files, and detects formats
    # from the bytes itself. Don't reintroduce either.
]
_extra_datas = []
for _dpkg in _data_pkgs:
    try:
        _extra_datas.extend(collect_data_files(_dpkg, include_py_files=False))
    except Exception:
        pass

# Packages whose data is collected SELECTIVELY — ``{package: [glob, ...]}``.
# A blanket ``collect_data_files`` would also drag in model weights we
# deliberately don't ship.
_filtered_data_pkgs = {
    # rapidocr resolves two YAMLs out of its OWN package directory via
    # ``Path(__file__).resolve().parent`` — which in a frozen build points into
    # the PYZ archive, i.e. at no path that exists on disk:
    #
    #   * ``default_models.yaml`` — read at IMPORT time, in a class body
    #     (``InferSession.model_info = OmegaConf.load(MODEL_URL_PATH)`` in
    #     ``rapidocr/inference_engine/base.py``). Merely importing the engine
    #     fails without it.
    #   * ``config.yaml`` — read by EVERY ``RapidOCR(...)`` construction:
    #     ``_load_config`` falls back to ``DEFAULT_CFG_PATH`` whenever no
    #     explicit ``config_path`` is passed, and we pass ``params=`` only
    #     (``integrations/parser_light_local._build_rapidocr``).
    #
    # ``collect_submodules`` only grabs .py and no pyinstaller-hooks-contrib
    # hook covers rapidocr, so without this entry a packaged build raises
    # ``FileNotFoundError: .../rapidocr/config.yaml`` on every image parse —
    # including for users who completed the model-download setup, because the
    # config load happens BEFORE the ``params`` overrides are merged.
    #
    # The bundled ``models/*.onnx`` (~16 MB) are deliberately NOT collected:
    # image OCR is a ``needs_setup`` capability whose weights are downloaded to
    # ``~/.valuz-oss/models/light_local/rapidocr/`` and handed in by absolute
    # path. Shipping them would bloat the bundle for files we never read.
    "rapidocr": ["config.yaml", "default_models.yaml"],
}
for _fpkg, _includes in _filtered_data_pkgs.items():
    try:
        _extra_datas.extend(
            collect_data_files(_fpkg, include_py_files=False, includes=_includes)
        )
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

    _exe_suffix = '.exe' if sys.platform == 'win32' else ''

    # Bundled Claude CLI (claude_agent_sdk/_bundled/claude[.exe])
    _claude_dir = os.path.join(_internal, 'claude_agent_sdk', '_bundled')
    if os.path.isfile(os.path.join(_claude_dir, 'claude' + _exe_suffix)):
        os.environ['PATH'] = _claude_dir + os.pathsep + os.environ.get('PATH', '')

    # Bundled Codex CLI (codex_cli_bin/bin/codex[.exe])
    _codex_dir = os.path.join(_internal, 'codex_cli_bin', 'bin')
    if os.path.isfile(os.path.join(_codex_dir, 'codex' + _exe_suffix)):
        os.environ['PATH'] = _codex_dir + os.pathsep + os.environ.get('PATH', '')

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
        # Built-in parser plugins — a sibling top-level ``plugins`` package
        # (``plugins/parser/<id>/``) the registry discovers DYNAMICALLY via
        # ``importlib.import_module("plugins.parser")`` + ``pkgutil.iter_modules``.
        # PyInstaller's static analysis can't follow that string import, so
        # without bundling the tree the frozen build raises
        # ``ModuleNotFoundError: No module named 'plugins'`` and the registry
        # comes up with ZERO parsers (every attachment/KB parse then fails).
        # Same raw-.py-via-sys.path strategy as ``valuz_agent``; the plugins'
        # own third-party deps (anydoc / pymupdf4llm / rapidocr / …) are
        # already collected through ``_third_party_pkgs`` above.
        (str(HERE / "plugins"), "plugins"),
        # Vendored kernel — same strategy; kernel/__init__.py injects its
        # own path for bare imports (src.*, app.*).
        (str(HERE / "kernel"), "kernel"),
        # Vendored tiktoken vocab for the goal-mode length fence. tiktoken caches
        # a vocab blob under ``$TIKTOKEN_CACHE_DIR/<sha1(blob_url)>`` and reads it
        # before downloading; we ship that file so the offline packaged app counts
        # tokens with no network. At runtime ``_vendored_tiktoken_cache_dir`` points
        # ``TIKTOKEN_CACHE_DIR`` at this bundled dir (``_MEIPASS/vendor/tiktoken``).
        # Refresh with ``scripts/download-tiktoken.sh``.
        (str(HERE / "vendor" / "tiktoken"), "vendor/tiktoken"),
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
    # Force Python UTF-8 mode (PEP 540) in the frozen interpreter: every
    # open()/read_text() without an explicit encoding= otherwise decodes with
    # the OS locale codepage (GBK on zh-CN Windows, CP932 on ja-JP, ...), so
    # any UTF-8 config/resource read on such a machine dies with
    # UnicodeDecodeError. mac/linux already run UTF-8 locales; this makes
    # Windows behave the same. Python defaults to UTF-8 mode from 3.15
    # (PEP 686) — this just enables it early.
    [("X utf8_mode=1", None, "OPTION")],
    exclude_binaries=True,
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=strip_binaries,
    upx=False,
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
    strip=strip_binaries,
    upx=False,
    name="valuz-server",
)
