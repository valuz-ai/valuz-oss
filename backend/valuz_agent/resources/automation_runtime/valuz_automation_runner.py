#!/usr/bin/env python3
"""Valuz code-automation bootstrap.

Copied into every run directory and launched as::

    python3 _valuz_runner.py --ctx ctx.json --entry <entry.py> --output output.json

It imports the entry module, calls ``run(ctx)`` and writes the wrapper object
``{"artifact": {...}, "files": [...]}`` to the output file. Standard library
only, on purpose: it must run on whatever ``python3`` the environment has.

Exit codes: 0 success · 2 contract violation (no ``run``, non-object result,
missing ``artifact``) · 3 the entry raised (traceback on stderr).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import os
import sys
import traceback

EXIT_OK = 0
EXIT_CONTRACT = 2
EXIT_ENTRY = 3


def _fail(code: int, message: str) -> int:
    sys.stderr.write(f"valuz-automation-runner: {message}\n")
    sys.stderr.flush()
    return code


def _load_output_file(path: str):  # type: ignore[no-untyped-def]
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError(f"output file is not valid JSON: {exc}") from exc
    return None


def _check_wrapper(wrapper) -> str | None:  # type: ignore[no-untyped-def]
    if not isinstance(wrapper, dict):
        return "run(ctx) must return an object like {'artifact': {...}}"
    if "artifact" not in wrapper:
        return "the returned object has no 'artifact' key"
    if not isinstance(wrapper["artifact"], dict):
        return "'artifact' must be a JSON object"
    files = wrapper.get("files")
    if files is not None:
        if not isinstance(files, list):
            return "'files' must be a list of {sourcePath, name?, mimeType?}"
        for entry in files:
            if not isinstance(entry, dict) or not isinstance(entry.get("sourcePath"), str):
                return "each file needs a string 'sourcePath'"
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ctx", required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    with open(args.ctx, encoding="utf-8") as fh:
        ctx = json.load(fh)

    entry = os.path.abspath(args.entry)
    if not os.path.isfile(entry):
        return _fail(EXIT_CONTRACT, f"entry file not found: {entry}")
    sys.path.insert(0, os.path.dirname(entry))
    module_name = "valuz_automation_entry"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        return _fail(EXIT_CONTRACT, f"cannot import entry: {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:  # noqa: BLE001 - the entry's own failure
        traceback.print_exc()
        return EXIT_ENTRY

    run = getattr(module, "run", None)
    if not callable(run):
        return _fail(EXIT_CONTRACT, "entry must define run(ctx)")

    try:
        result = run(ctx)
        if inspect.iscoroutine(result):
            result = asyncio.run(result)
    except SystemExit as exc:  # the program decided; keep its code
        code = exc.code if isinstance(exc.code, int) else EXIT_ENTRY
        return code or EXIT_OK
    except BaseException:  # noqa: BLE001 - the entry's own failure
        traceback.print_exc()
        return EXIT_ENTRY

    # A file the program wrote itself wins over the return value: that is how
    # a large or file-backed result avoids living in memory twice.
    try:
        wrapper = _load_output_file(args.output)
    except ValueError as exc:
        return _fail(EXIT_CONTRACT, str(exc))
    if wrapper is None:
        wrapper = result
    problem = _check_wrapper(wrapper)
    if problem:
        return _fail(EXIT_CONTRACT, problem)

    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(wrapper, fh, ensure_ascii=False)
    os.replace(tmp, args.output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
