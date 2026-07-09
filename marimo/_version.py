# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # Fork: the distribution is named marimoSH05 (see pyproject.toml)
    __version__ = version("marimoSH05")
except PackageNotFoundError:
    try:
        __version__ = version("marimo")
    except PackageNotFoundError:
        # package is not installed
        __version__ = "unknown"
