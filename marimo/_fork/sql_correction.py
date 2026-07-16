# Copyright 2026 Marimo. All rights reserved.
"""ERD-based self-correction for SQL errors (fork, see FORK.md).

When a query against the mounted data source references a table or column
that does not exist, the database error is enriched with the nearest REAL
names from the user's ERD ("did you mean ..."). The hint lands in the cell
error and in what the "Fix with AI" button sends to the model, so the
correct identifier is one click away - a deterministic learning loop that
never depends on the model guessing.

We surface suggestions rather than silently rewriting and retrying: a
fuzzy match could pick the wrong column and produce confidently wrong
results, which is worse than a clear error.
"""

from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Optional

from marimo import _loggers

LOGGER = _loggers.marimo_logger()

# Postgres / DuckDB "does not exist" patterns
_TABLE_RE = re.compile(
    r'(?:relation|table)(?: with name)? "?([\w.]+)"? does not exist',
    re.IGNORECASE,
)
_COLUMN_RE = re.compile(r'column "?([\w.]+)"? does not exist', re.IGNORECASE)

_MAX_SUGGESTIONS = 6


def suggest_from_erd(error_message: str) -> Optional[str]:
    """A 'did you mean' hint, or None. Never raises.

    Prefers the authoritative full schema catalog (all tables); falls
    back to the user's ERD. Name kept for backward compatibility.
    """
    hint = _suggest_from_catalog(error_message)
    if hint:
        return hint
    try:
        from marimo._fork.erd import load_erd

        index = load_erd()
        if index is None or not index.is_graph:
            return None

        table_match = _TABLE_RE.search(error_message)
        if table_match:
            return _suggest_tables(table_match.group(1), index)

        column_match = _COLUMN_RE.search(error_message)
        if column_match:
            return _suggest_columns(column_match.group(1), index)
    except Exception as e:
        LOGGER.warning("ERD suggestion failed: %s", e)
    return None


def _suggest_from_catalog(error_message: str) -> Optional[str]:
    """'Did you mean' from the authoritative schema catalog, or None."""
    try:
        from marimo._fork import schema_catalog

        if schema_catalog.load_catalog() is None:
            return None

        table_match = _TABLE_RE.search(error_message)
        if table_match:
            bad = table_match.group(1).split(".")[-1]
            candidates = schema_catalog.find_table(bad)
            if candidates:
                return (
                    f"'{bad}' is not a table in the database. Closest real "
                    f"tables: {', '.join(candidates)}. Use one of these "
                    "exact names."
                )
            return None

        column_match = _COLUMN_RE.search(error_message)
        if column_match:
            segments = column_match.group(1).split(".")
            bad = segments[-1]
            qualifier = segments[-2] if len(segments) >= 2 else None
            candidates = schema_catalog.find_column(bad, table=qualifier)
            if not candidates:
                return None
            where = f" on {qualifier}" if qualifier else ""
            return (
                f"'{bad}' is not a column{where}. Closest real columns "
                f"(from the live schema): {', '.join(candidates)}. Use one "
                "of these exact names - do not invent column names."
            )
    except Exception as e:
        LOGGER.warning("catalog suggestion failed: %s", e)
    return None


def _rank(bad: str, universe: list[str]) -> list[str]:
    lowered = bad.lower()
    close = get_close_matches(bad, universe, n=_MAX_SUGGESTIONS, cutoff=0.5)
    substr = [name for name in universe if lowered in name.lower()]
    # de-duplicate, preserve order (close matches first)
    ordered: list[str] = []
    for name in close + substr:
        if name not in ordered:
            ordered.append(name)
    return ordered[:_MAX_SUGGESTIONS]


def _suggest_tables(raw: str, index) -> Optional[str]:
    bad = raw.split(".")[-1]
    candidates = _rank(bad, list(index.tables))
    if not candidates:
        return None
    return (
        f"'{bad}' is not a table in the data model. Closest real tables "
        f"(from the ERD): {', '.join(candidates)}. Use one of these exact "
        "names - do not invent table names."
    )


def _suggest_columns(raw: str, index) -> Optional[str]:
    segments = raw.split(".")
    bad = segments[-1]
    qualifier = segments[-2] if len(segments) >= 2 else None

    column_to_tables: dict[str, list[str]] = {}
    for table, columns in index.tables.items():
        for column in columns:
            column_to_tables.setdefault(column, []).append(table)

    # If the error qualifies the column with a real table name
    # (`c_invoiceline.qtyentered`), that table's own columns are the
    # relevant suggestions - rank them first, since a global spelling
    # match can otherwise surface a same-named column on the wrong table.
    scoped: list[str] = []
    if qualifier and qualifier in index.tables:
        scoped = _rank(bad, index.tables[qualifier])

    ranked_global = _rank(bad, list(column_to_tables))
    candidates: list[str] = []
    for column in scoped + ranked_global:
        if column not in candidates:
            candidates.append(column)
    candidates = candidates[:_MAX_SUGGESTIONS]
    if not candidates:
        return None

    scoped_set = set(scoped)
    parts = []
    for column in candidates:
        if column in scoped_set:
            parts.append(f"{column} (on {qualifier})")
        else:
            tables = column_to_tables[column][:3]
            parts.append(f"{column} (in {', '.join(tables)})")
    return (
        f"'{bad}' is not a column in the data model. Closest real columns "
        f"(from the ERD): {'; '.join(parts)}. Use one of these exact names "
        "on the right table - do not invent column names."
    )
