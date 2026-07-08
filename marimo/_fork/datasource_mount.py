# Copyright 2026 Marimo. All rights reserved.
"""Session-level data source mounting (fork feature, see FORK.md).

A notebook session can be bound to a single primary data source at launch:

    marimo edit report.py --data-source postgresql://user:pw@host/db
    marimo edit report.py --data-source ./sales.csv
    marimo run report.py --data-source ./warehouse_seed.sql

The spec travels to the kernel subprocess via the `MARIMO_DATA_SOURCE`
environment variable, so it also works for plain script execution
(scheduled reports): `MARIMO_DATA_SOURCE=... python report.py`.

Once mounted, the connection:

- is injected into the kernel globals as `datasource`;
- appears in the datasources panel like any user-created engine;
- becomes the default engine for `mo.sql(...)` when no `engine=` is passed.

Supported specs:

- any SQLAlchemy URI (`postgresql://`, `mysql://`, `sqlite:///...`, ...)
- `.csv`, `.tsv`, `.parquet`, `.json`, `.jsonl`, `.ndjson` files — exposed
  as a DuckDB view named after the file
- `.sql` files — executed against a fresh in-memory DuckDB database
- `.db`, `.duckdb`, `.ddb` files — opened directly with DuckDB
- `.sqlite`, `.sqlite3` files — opened via SQLAlchemy
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from marimo import _loggers

if TYPE_CHECKING:
    from marimo._runtime.runtime import Kernel

LOGGER = _loggers.marimo_logger()

DATA_SOURCE_ENV_VAR = "MARIMO_DATA_SOURCE"
DATA_SOURCE_VARIABLE = "datasource"

_UNRESOLVED = object()
_mounted_connection: Any = _UNRESOLVED


def set_data_source(spec: str) -> None:
    """Record the session data source so child (kernel) processes inherit it."""
    os.environ[DATA_SOURCE_ENV_VAR] = spec


def get_mounted_connection() -> Optional[Any]:
    """Return the session's mounted connection, resolving it on first use.

    Never raises: resolution failures are logged and cached as "no mount"
    so that `mo.sql` can always fall back to its stock behavior.
    """
    global _mounted_connection
    if _mounted_connection is _UNRESOLVED:
        spec = os.environ.get(DATA_SOURCE_ENV_VAR, "").strip()
        if not spec:
            _mounted_connection = None
        else:
            try:
                _mounted_connection = _create_connection(spec)
                LOGGER.info("Mounted session data source: %s", spec)
            except Exception as e:
                _mounted_connection = None
                LOGGER.error(
                    "Failed to mount data source %r: %s", spec, e, exc_info=e
                )
    return _mounted_connection


def mount_session_data_source(kernel: Kernel) -> None:
    """Bind the mounted data source to a kernel session, if one is configured.

    Injects the connection into the kernel globals as `datasource` and
    broadcasts it so the frontend datasources panel picks it up without
    requiring a cell to define it.
    """
    connection = get_mounted_connection()
    if connection is None:
        return

    from marimo._messaging.notification import (
        DataSourceConnectionsNotification,
    )
    from marimo._messaging.notification_utils import broadcast_notification
    from marimo._sql.get_engines import (
        engine_to_data_source_connection,
        get_engines_from_variables,
    )
    from marimo._types.ids import VariableName

    variable = VariableName(DATA_SOURCE_VARIABLE)
    kernel.globals[variable] = connection

    try:
        engines = get_engines_from_variables([(variable, connection)])
        if engines:
            broadcast_notification(
                DataSourceConnectionsNotification(
                    connections=[
                        engine_to_data_source_connection(name, engine)
                        for name, engine in engines
                    ]
                )
            )
    except Exception as e:
        LOGGER.warning(
            "Mounted data source is not usable as a SQL engine: %s",
            e,
            exc_info=e,
        )


def _create_connection(spec: str) -> Any:
    if "://" in spec:
        return _sqlalchemy_engine(spec)

    path = Path(spec)
    if not path.exists():
        raise FileNotFoundError(f"Data source file not found: {spec}")

    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".parquet", ".json", ".jsonl", ".ndjson"):
        return _duckdb_view_over_file(path)
    if suffix == ".sql":
        return _duckdb_from_sql_script(path)
    if suffix in (".db", ".duckdb", ".ddb"):
        import duckdb

        return duckdb.connect(str(path))
    if suffix in (".sqlite", ".sqlite3"):
        return _sqlalchemy_engine(f"sqlite:///{path.as_posix()}")

    raise ValueError(
        f"Unsupported data source: {spec}. Expected a database URI or a "
        ".csv/.tsv/.parquet/.json/.sql/.db/.duckdb/.sqlite file."
    )


def _sqlalchemy_engine(uri: str) -> Any:
    import sqlalchemy

    engine = sqlalchemy.create_engine(uri)
    # Fail fast at mount time instead of at first query
    with engine.connect():
        pass
    return engine


def _table_name_for(path: Path) -> str:
    name = re.sub(r"\W", "_", path.stem)
    if not name or name[0].isdigit():
        name = f"data_{name}"
    return name


def _duckdb_view_over_file(path: Path) -> Any:
    import duckdb

    readers = {
        ".csv": "read_csv_auto",
        ".tsv": "read_csv_auto",
        ".parquet": "read_parquet",
        ".json": "read_json_auto",
        ".jsonl": "read_json_auto",
        ".ndjson": "read_json_auto",
    }
    reader = readers[path.suffix.lower()]
    connection = duckdb.connect(":memory:")
    table = _table_name_for(path)
    connection.execute(
        f"CREATE OR REPLACE VIEW {table} AS "
        f"SELECT * FROM {reader}('{path.as_posix()}')"
    )
    return connection


def _duckdb_from_sql_script(path: Path) -> Any:
    import duckdb

    connection = duckdb.connect(":memory:")
    connection.execute(path.read_text(encoding="utf-8"))
    return connection
