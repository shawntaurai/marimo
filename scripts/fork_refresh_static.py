# /// script
# requires-python = ">=3.11"
# ///
"""Refresh marimo/_static and marimo/_lsp from the official PyPI wheel.

Fork-maintenance helper (see FORK.md). npmjs.org is blocked on this
machine, so the frontend cannot be built locally; instead we take the
prebuilt assets from the PyPI wheel matching the checked-out version.

Usage:
    python scripts/fork_refresh_static.py [VERSION]

If VERSION is omitted, it is read from pyproject.toml.
"""
# ruff: noqa: T201

from __future__ import annotations

import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("marimo/_static", "marimo/_lsp")


def resolve_version() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text("utf-8")
    )
    return pyproject["project"]["version"]


def wheel_url(version: str) -> str:
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/marimo/{version}/json"
    ) as resp:
        meta = json.load(resp)
    for file in meta["urls"]:
        if file["packagetype"] == "bdist_wheel":
            return file["url"]
    raise SystemExit(f"No wheel found on PyPI for marimo=={version}")


def main() -> None:
    version = resolve_version()
    url = wheel_url(version)
    print(f"Downloading {url} ...")
    with urllib.request.urlopen(url) as resp:
        wheel = zipfile.ZipFile(io.BytesIO(resp.read()))

    for target in TARGETS:
        dest = REPO_ROOT / target
        if dest.exists():
            shutil.rmtree(dest)
        names = [n for n in wheel.namelist() if n.startswith(target + "/")]
        if not names:
            raise SystemExit(f"{target} not found inside the wheel")
        wheel.extractall(REPO_ROOT, members=names)
        print(f"Extracted {len(names)} files -> {dest}")

    print(f"Done: assets refreshed from marimo=={version}")


if __name__ == "__main__":
    main()
