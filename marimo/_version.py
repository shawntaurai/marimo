# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _checkout_version() -> str | None:
    """Version from the checkout's pyproject.toml, for editable installs.

    Fork: after an upstream sync bumps pyproject.toml, dist metadata is
    stale until `pip install -e .` is re-run — which is impossible while
    the server is running (the launcher .exe is locked). Reading the
    checkout directly keeps the reported version truthful either way.
    """
    try:
        import pathlib

        import tomllib

        pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
        if pyproject.exists():
            project = tomllib.loads(pyproject.read_text("utf-8"))["project"]
            # only trust it if it is actually this project's checkout
            if project.get("name") in ("dedomena", "marimo"):
                return str(project["version"])
    except Exception:
        pass
    return None


__version__ = _checkout_version() or "unknown"
if __version__ == "unknown":
    try:
        # Fork: the distribution is named dedomena (see pyproject.toml)
        __version__ = version("dedomena")
    except PackageNotFoundError:
        try:
            __version__ = version("marimo")
        except PackageNotFoundError:
            # package is not installed
            __version__ = "unknown"
