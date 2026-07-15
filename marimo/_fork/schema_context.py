# Copyright 2026 Marimo. All rights reserved.
"""Schema-aware AI context for the mounted data source (fork, see FORK.md).

When a session is launched with `--data-source`, every AI chat prompt is
augmented with an introspected description of that source: schemas, tables,
columns with types, primary keys, and foreign-key relationships. This lets
the assistant translate natural-language questions ("show me all purchase
orders that became sales invoices in June") into SQL against the mounted
database without the user pasting schemas around.

Introspection runs in the server process over its own lazily-created
connection (the spec travels via the `MARIMO_DATA_SOURCE` env var) and is
cached for `_CACHE_TTL_SECONDS`.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from marimo import _loggers
from marimo._fork.datasource_mount import (
    DATA_SOURCE_VARIABLE,
    get_mounted_connection,
)

# Optional user-supplied ERD describing the logical data model (parsing and
# lookup live in marimo._fork.erd; .pgerd exports are indexed for the
# get_erd_relationships AI tool). Re-exported here for the CLI and tests.
from marimo._fork.erd import ERD_ENV_VAR, set_erd  # noqa: F401

LOGGER = _loggers.marimo_logger()

_CACHE_TTL_SECONDS = 300
# Keep the schema section bounded so small local models aren't drowned;
# override with MARIMO_SCHEMA_CONTEXT_MAX_CHARS.
_DEFAULT_MAX_CHARS = 12_000

_ERD_DEFAULT_MAX_CHARS = 8_000

_SYSTEM_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "pg_toast",
    "crdb_internal",
}

_cache: tuple[float, str] | None = None


def get_mounted_schema_section(question: str | None = None) -> str:
    """Prompt sections for the mounted data source and/or its ERD, or "".

    When the user supplies an ERD (`--erd`), it is the source of truth for
    the data model: the whole-database indexing is skipped and the model is
    instructed to query the database itself (information_schema, sample
    rows) for any column details it needs. Set
    `MARIMO_ERD_REPLACES_SCHEMA=0` to include both.

    When `question` is given (the user's prompt), an ERD excerpt focused
    on that question — matching tables with columns and join paths — is
    appended so one-shot generation has real names to work with.

    Never raises; introspection failures degrade to an empty section.
    The ERD file is re-read on every call (cheap), so edits to it apply
    to the next chat message without restarting the server.
    """
    erd = _erd_section()
    if erd and os.environ.get("MARIMO_ERD_REPLACES_SCHEMA", "1") != "0":
        return _erd_mode_header() + erd + _focused_section(question)

    global _cache
    if _cache is not None and time.time() - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1] + erd

    section = ""
    try:
        connection = get_mounted_connection()
        if connection is not None:
            schema_text = _describe_connection(connection)
            if schema_text:
                section = _build_section(schema_text)
    except Exception as e:
        LOGGER.warning(
            "Failed to introspect mounted data source for AI context: %s",
            e,
            exc_info=e,
        )
    _cache = (time.time(), section)
    return section + erd


def _erd_mode_header() -> str:
    """Light data-source header used when the ERD carries the data model."""
    dialect = "unknown"
    try:
        connection = get_mounted_connection()
        if connection is not None:
            dialect = _dialect_name(connection)
    except Exception:
        pass
    if dialect == "unknown":
        return ""
    return (
        "\n\n<mounted_data_source>\n"
        f"This session is bound to a primary data source "
        f"(dialect: {dialect}).\n"
        f"- It is available in the notebook as the variable "
        f"`{DATA_SOURCE_VARIABLE}`.\n"
        "- `mo.sql(...)` uses it as the default engine, so SQL cells run "
        "against it directly; when an explicit engine is required use "
        f"`engine={DATA_SOURCE_VARIABLE}`.\n"
        "- The user supplied an ERD (next section) that documents the data "
        "model; derive table names and joins from it.\n"
        "- The full schema is intentionally NOT included. When you need "
        "column names or types for a table, first run a small query "
        "against the database (e.g. information_schema.columns for that "
        "table, or SELECT * ... LIMIT 5), then write the final query.\n"
        "</mounted_data_source>"
    )


def _dialect_name(connection: Any) -> str:
    try:
        import sqlalchemy

        if isinstance(connection, sqlalchemy.engine.Engine):
            return str(connection.dialect.name)
    except ModuleNotFoundError:
        pass
    try:
        import duckdb

        if isinstance(connection, duckdb.DuckDBPyConnection):
            return "duckdb"
    except ModuleNotFoundError:
        pass
    return "unknown"


def _focused_section(question: str | None) -> str:
    if not question:
        return ""
    try:
        from marimo._fork.erd import focused_context

        excerpt = focused_context(question)
        if not excerpt:
            return ""
        return f"\n\n<erd_excerpt_for_this_question>\n{excerpt}\n</erd_excerpt_for_this_question>"
    except Exception as e:
        LOGGER.warning("Failed to build focused ERD excerpt: %s", e)
        return ""


def _erd_section() -> str:
    """Prompt section with the user-supplied ERD, or "" if none/unusable."""
    try:
        from marimo._fork.erd import load_erd, render_for_prompt

        index = load_erd()
        if index is None:
            return ""
        max_chars = int(
            os.environ.get("MARIMO_ERD_MAX_CHARS", _ERD_DEFAULT_MAX_CHARS)
        )
        body = render_for_prompt(index, max_chars)
        if not body:
            return ""
        return (
            "\n\n<data_model_erd>\n"
            "The following entity-relationship data documents the "
            "logical data model, including relationships that may NOT be "
            "declared as database constraints. When translating questions "
            "into SQL, derive joins from these relationships — they take "
            "precedence over guessing.\n\n"
            f"{body}\n"
            "</data_model_erd>"
        )
    except Exception as e:
        LOGGER.warning("Failed to read ERD file for AI context: %s", e)
        return ""


def _build_section(schema_text: str) -> str:
    max_chars = int(
        os.environ.get("MARIMO_SCHEMA_CONTEXT_MAX_CHARS", _DEFAULT_MAX_CHARS)
    )
    if len(schema_text) > max_chars:
        schema_text = (
            schema_text[:max_chars]
            + "\n... (schema truncated; query information_schema for more)"
        )
    return (
        "\n\n<mounted_data_source>\n"
        "This session is bound to a primary data source. Its schema is "
        "described below.\n"
        f"- It is available in the notebook as the variable `{DATA_SOURCE_VARIABLE}`.\n"
        "- `mo.sql(...)` uses it as the default engine, so SQL cells run "
        "against it directly; when an explicit engine is required use "
        f"`engine={DATA_SOURCE_VARIABLE}`.\n"
        "- When the user asks a question about the data in natural "
        "language, translate it into SQL against this schema (use the "
        "foreign-key relationships to join tables) and answer with a SQL "
        "cell.\n\n"
        f"{schema_text.strip()}\n"
        "</mounted_data_source>"
    )


def _describe_connection(connection: Any) -> Optional[str]:
    try:
        import sqlalchemy

        if isinstance(connection, sqlalchemy.engine.Engine):
            return _describe_sqlalchemy(connection)
    except ModuleNotFoundError:
        pass

    try:
        import duckdb

        if isinstance(connection, duckdb.DuckDBPyConnection):
            return _describe_duckdb(connection)
    except ModuleNotFoundError:
        pass

    return None


def _describe_sqlalchemy(engine: Any) -> str:
    import sqlalchemy

    inspector = sqlalchemy.inspect(engine)
    lines = [f"Dialect: {engine.dialect.name}"]
    relationships: list[str] = []

    try:
        schemas = [
            s for s in inspector.get_schema_names() if s not in _SYSTEM_SCHEMAS
        ]
    except Exception:
        schemas = [None]

    for schema in schemas:
        table_names = inspector.get_table_names(schema=schema)
        view_names: list[str] = []
        try:
            view_names = inspector.get_view_names(schema=schema)
        except Exception:
            pass
        if not table_names and not view_names:
            continue

        prefix = f"{schema}." if schema else ""
        lines.append(f"\nSchema: {schema or '(default)'}")
        for table in table_names + view_names:
            qualified = f"{prefix}{table}"
            try:
                pk_cols = set(
                    (
                        inspector.get_pk_constraint(table, schema=schema) or {}
                    ).get("constrained_columns", [])
                )
            except Exception:
                pk_cols = set()

            column_specs = []
            for col in inspector.get_columns(table, schema=schema):
                spec = f"{col['name']} {col['type']}"
                if col["name"] in pk_cols:
                    spec += " PK"
                column_specs.append(spec)
            kind = " (view)" if table in view_names else ""
            lines.append(f"- {qualified}{kind}({', '.join(column_specs)})")

            try:
                for fk in inspector.get_foreign_keys(table, schema=schema):
                    ref_schema = fk.get("referred_schema")
                    ref_prefix = f"{ref_schema}." if ref_schema else ""
                    relationships.append(
                        f"- {qualified}({', '.join(fk['constrained_columns'])})"
                        f" -> {ref_prefix}{fk['referred_table']}"
                        f"({', '.join(fk['referred_columns'])})"
                    )
            except Exception:
                pass

    if relationships:
        lines.append("\nRelationships (foreign keys):")
        lines.extend(relationships)
    return "\n".join(lines)


def _describe_duckdb(connection: Any) -> str:
    lines = ["Dialect: duckdb"]

    columns = connection.execute(
        """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name, ordinal_position
        """
    ).fetchall()

    constraints: dict[tuple[str, str], dict[str, Any]] = {}
    fk_texts: list[str] = []
    try:
        for schema, table, ctype, ctext, ccols in connection.execute(
            """
            SELECT schema_name, table_name, constraint_type,
                   constraint_text, constraint_column_names
            FROM duckdb_constraints()
            """
        ).fetchall():
            if ctype == "PRIMARY KEY":
                constraints.setdefault((schema, table), {})["pk"] = set(
                    ccols or []
                )
            elif ctype == "FOREIGN KEY":
                fk_texts.append(f"- {schema}.{table}: {ctext}")
    except Exception:
        pass

    tables: dict[tuple[str, str], list[str]] = {}
    for schema, table, column, data_type in columns:
        pk_cols = constraints.get((schema, table), {}).get("pk", set())
        spec = f"{column} {data_type}"
        if column in pk_cols:
            spec += " PK"
        tables.setdefault((schema, table), []).append(spec)

    current_schema = None
    for (schema, table), column_specs in tables.items():
        if schema != current_schema:
            lines.append(f"\nSchema: {schema}")
            current_schema = schema
        lines.append(f"- {schema}.{table}({', '.join(column_specs)})")

    if fk_texts:
        lines.append("\nRelationships (foreign keys):")
        lines.extend(fk_texts)
    return "\n".join(lines)
