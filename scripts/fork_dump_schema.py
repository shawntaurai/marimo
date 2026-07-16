# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlalchemy", "psycopg2-binary"]
# ///
"""Dump the COMPLETE schema of the mounted database to JSON.

Produces a full, authoritative "instance" of the database structure -
every table, every column with type/nullable/PK, and every foreign key -
so the AI can look up any table in the database on demand (see
marimo/_fork/schema_catalog.py). This is the whole database's schema,
not a curated subset.

Usage:
    python scripts/fork_dump_schema.py [--data-source URI] [-o out.json]

Default output: <marimo config dir>/dedomena_schema.json
"""
# ruff: noqa: T201

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def default_output() -> Path:
    from marimo._config.utils import get_or_create_user_config_path

    return (
        Path(get_or_create_user_config_path()).parent / "dedomena_schema.json"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-source", default=None)
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--schema", default="public")
    args = parser.parse_args()

    uri = args.data_source or os.environ.get("MARIMO_DATA_SOURCE", "")
    if "://" not in uri:
        sys.exit("Provide --data-source or set MARIMO_DATA_SOURCE.")

    import sqlalchemy

    engine = sqlalchemy.create_engine(uri, pool_pre_ping=True)
    sch = args.schema
    tables: dict[str, dict] = {}

    with engine.connect() as conn:
        text = sqlalchemy.text
        print("Reading columns ...")
        for t, col, dtype, nullable, pos in conn.execute(
            text(
                """SELECT table_name, column_name, data_type, is_nullable,
                          ordinal_position
                   FROM information_schema.columns
                   WHERE table_schema = :s
                   ORDER BY table_name, ordinal_position"""
            ),
            {"s": sch},
        ):
            tables.setdefault(t, {"columns": [], "pk": [], "fks": []})[
                "columns"
            ].append(
                {"name": col, "type": dtype, "nullable": nullable == "YES"}
            )

        print("Reading primary keys ...")
        for t, col in conn.execute(
            text(
                """SELECT tc.table_name, kcu.column_name
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                   WHERE tc.constraint_type = 'PRIMARY KEY'
                     AND tc.table_schema = :s"""
            ),
            {"s": sch},
        ):
            if t in tables:
                tables[t]["pk"].append(col)

        print("Reading foreign keys ...")
        for t, col, ft, fcol in conn.execute(
            text(
                """SELECT tc.table_name, kcu.column_name,
                          ccu.table_name AS ref_table,
                          ccu.column_name AS ref_column
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                   JOIN information_schema.constraint_column_usage ccu
                     ON tc.constraint_name = ccu.constraint_name
                    AND tc.table_schema = ccu.table_schema
                   WHERE tc.constraint_type = 'FOREIGN KEY'
                     AND tc.table_schema = :s"""
            ),
            {"s": sch},
        ):
            if t in tables:
                tables[t]["fks"].append(
                    {"column": col, "ref_table": ft, "ref_column": fcol}
                )

    out = Path(args.output) if args.output else default_output()
    doc = {
        "dialect": engine.dialect.name,
        "schema": sch,
        "table_count": len(tables),
        "tables": tables,
    }
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    ncols = sum(len(t["columns"]) for t in tables.values())
    nfks = sum(len(t["fks"]) for t in tables.values())
    print(
        f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB): "
        f"{len(tables)} tables, {ncols} columns, {nfks} foreign keys."
    )


if __name__ == "__main__":
    main()
