# Copyright 2026 Marimo. All rights reserved.
"""Complete database schema catalog (fork, see FORK.md).

Loads the full schema JSON produced by `scripts/fork_dump_schema.py`
(every table, column, type, PK and FK of the mounted database - large
ERPs run to hundreds of tables and tens of thousands of columns). The
whole thing is far too large to inject into a prompt, so it is retrieved
on demand: the `get_table_schema` AI tool and the error-correction path
look up exactly the tables a question needs. This is how the model has
access to the WHOLE database, not a curated subset.

The catalog is authoritative (introspected live) and supersedes the ERD
for column lookups; the ERD still supplies the user's curated
relationship view.
"""

from __future__ import annotations

import json
import os
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Optional

from marimo import _loggers

LOGGER = _loggers.marimo_logger()

SCHEMA_ENV_VAR = "MARIMO_DB_SCHEMA_JSON"

_cache: tuple[str, float, dict[str, Any]] | None = None


def _schema_path() -> Optional[Path]:
    override = os.environ.get(SCHEMA_ENV_VAR, "").strip()
    if override:
        return Path(override)
    try:
        from marimo._config.utils import get_or_create_user_config_path

        return (
            Path(get_or_create_user_config_path()).parent
            / "dedomena_schema.json"
        )
    except Exception:
        return None


def load_catalog() -> Optional[dict[str, Any]]:
    """The full schema doc (cached by mtime), or None. Never raises."""
    global _cache
    try:
        path = _schema_path()
        if path is None or not path.exists():
            return None
        mtime = path.stat().st_mtime
        if (
            _cache is not None
            and _cache[0] == str(path)
            and _cache[1] == mtime
        ):
            return _cache[2]
        doc = json.loads(path.read_text(encoding="utf-8"))
        _cache = (str(path), mtime, doc)
        return doc
    except Exception as e:
        LOGGER.warning("Failed to load schema catalog: %s", e)
        return None


def all_table_names() -> list[str]:
    doc = load_catalog()
    return sorted(doc["tables"]) if doc else []


def get_table(name: str) -> Optional[dict[str, Any]]:
    doc = load_catalog()
    if not doc:
        return None
    return doc["tables"].get(name)


def describe_tables(names: list[str], max_tables: int = 12) -> str:
    """Exact columns (with types/PK) and FKs for the named tables."""
    doc = load_catalog()
    if not doc:
        return ""
    lines: list[str] = []
    for name in names[:max_tables]:
        table = doc["tables"].get(name)
        if not table:
            lines.append(f"{name}: (no such table in the database)")
            continue
        pk = set(table.get("pk", []))
        cols = ", ".join(
            f"{c['name']} {c['type']}" + (" PK" if c["name"] in pk else "")
            for c in table["columns"]
        )
        lines.append(f"{name}({cols})")
        for fk in table.get("fks", []):
            lines.append(
                f"  FK {name}.{fk['column']} -> "
                f"{fk['ref_table']}.{fk['ref_column']}"
            )
    return "\n".join(lines)


def find_column(bad: str, table: Optional[str] = None) -> list[str]:
    """Nearest real column names to `bad`, optionally scoped to a table."""
    doc = load_catalog()
    if not doc:
        return []
    if table and table in doc["tables"]:
        universe = [c["name"] for c in doc["tables"][table]["columns"]]
    else:
        universe = sorted(
            {c["name"] for t in doc["tables"].values() for c in t["columns"]}
        )
    lowered = bad.lower()
    close = get_close_matches(bad, universe, n=6, cutoff=0.5)
    substr = [c for c in universe if lowered in c.lower()]
    ordered: list[str] = []
    for c in close + substr:
        if c not in ordered:
            ordered.append(c)
    return ordered[:6]


def find_table(bad: str) -> list[str]:
    names = all_table_names()
    if not names:
        return []
    lowered = bad.lower()
    close = get_close_matches(bad, names, n=6, cutoff=0.5)
    substr = [t for t in names if lowered in t.lower()]
    ordered: list[str] = []
    for t in close + substr:
        if t not in ordered:
            ordered.append(t)
    return ordered[:6]
