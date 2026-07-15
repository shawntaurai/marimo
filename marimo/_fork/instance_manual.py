# Copyright 2026 Marimo. All rights reserved.
"""Instance operating manual for AI prompts (fork, see FORK.md).

If `dedomena_manual.md` exists in the marimo config directory (override
with the `MARIMO_AI_MANUAL` env var), its contents ride along in every
AI prompt — chat and cell generation — for whatever model the instance
uses. This is where the deployment's tribal knowledge lives: business
vocabulary, data-model facts, execution conventions. Edit the file and
the next prompt uses it; no restart needed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from marimo import _loggers

LOGGER = _loggers.marimo_logger()

MANUAL_ENV_VAR = "MARIMO_AI_MANUAL"
_MAX_CHARS = 16_000

_cache: tuple[str, float, str] | None = None


def _manual_path() -> Optional[Path]:
    override = os.environ.get(MANUAL_ENV_VAR, "").strip()
    if override:
        return Path(override)
    # under pytest, never pick up the developer machine's real manual
    # (tests opt in by setting MARIMO_AI_MANUAL to a fixture file)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        from marimo._config.utils import get_or_create_user_config_path

        return (
            Path(get_or_create_user_config_path()).parent
            / "dedomena_manual.md"
        )
    except Exception:
        return None


def get_manual_section() -> str:
    """Prompt section with the instance manual, or "" when absent.

    Cached by mtime; never raises.
    """
    global _cache
    try:
        path = _manual_path()
        if path is None or not path.exists():
            return ""
        key, mtime = str(path), path.stat().st_mtime
        if _cache is not None and _cache[0] == key and _cache[1] == mtime:
            return _cache[2]

        text = path.read_text(encoding="utf-8", errors="replace").strip()
        section = ""
        if text:
            if len(text) > _MAX_CHARS:
                text = text[:_MAX_CHARS] + "\n... (manual truncated)"
            section = (
                "\n\n<instance_operating_manual>\n"
                "Follow this deployment-specific manual when answering:\n\n"
                f"{text}\n"
                "</instance_operating_manual>"
            )
        _cache = (key, mtime, section)
        return section
    except Exception as e:
        LOGGER.warning("Failed to read instance manual: %s", e)
        return ""
