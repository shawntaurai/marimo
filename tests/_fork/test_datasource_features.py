# Copyright 2026 Marimo. All rights reserved.
"""Tests for fork features: data source mounting + schema AI context."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from marimo._dependencies.dependencies import DependencyManager
from marimo._fork import datasource_mount, schema_context
from marimo._runtime.commands import (
    CreateNotebookCommand,
    UpdateUIElementCommand,
)
from tests.conftest import MockedKernel

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

HAS_DUCKDB = DependencyManager.duckdb.has()
HAS_SQLALCHEMY = DependencyManager.sqlalchemy.has()

ERP_SEED = """
CREATE TABLE purchase_orders (
  po_number VARCHAR PRIMARY KEY,
  supplier VARCHAR,
  order_date DATE
);
CREATE TABLE goods_received (
  grv_number VARCHAR PRIMARY KEY,
  po_number VARCHAR REFERENCES purchase_orders(po_number),
  received_date DATE
);
CREATE TABLE sales_invoices (
  invoice_number VARCHAR PRIMARY KEY,
  grv_number VARCHAR REFERENCES goods_received(grv_number),
  invoice_date DATE,
  amount DECIMAL(12,2)
);
INSERT INTO purchase_orders VALUES ('PO-100', 'acme', '2026-06-01');
INSERT INTO goods_received VALUES ('GRV-55', 'PO-100', '2026-06-05');
INSERT INTO sales_invoices VALUES ('INV-9', 'GRV-55', '2026-06-10', 1500.00);
"""


@pytest.fixture(autouse=True)
def reset_mount() -> Generator[None, None, None]:
    """Isolate each test from the mount caches and env vars."""
    saved = os.environ.pop(datasource_mount.DATA_SOURCE_ENV_VAR, None)
    saved_erd = os.environ.pop(schema_context.ERD_ENV_VAR, None)
    datasource_mount._mounted_connection = datasource_mount._UNRESOLVED
    schema_context._cache = None
    yield
    datasource_mount._mounted_connection = datasource_mount._UNRESOLVED
    schema_context._cache = None
    for env_var, value in (
        (datasource_mount.DATA_SOURCE_ENV_VAR, saved),
        (schema_context.ERD_ENV_VAR, saved_erd),
    ):
        if value is None:
            os.environ.pop(env_var, None)
        else:
            os.environ[env_var] = value


def _mount(spec: str) -> None:
    datasource_mount.set_data_source(spec)


def _instantiate_request() -> CreateNotebookCommand:
    return CreateNotebookCommand(
        execution_requests=(),
        cell_ids=(),
        set_ui_element_value_request=UpdateUIElementCommand(
            object_ids=[], values=[]
        ),
        auto_run=True,
    )


def test_no_mount_resolves_to_none() -> None:
    assert datasource_mount.get_mounted_connection() is None
    assert schema_context.get_mounted_schema_section() == ""


def test_bad_spec_never_raises() -> None:
    _mount("does_not_exist.xyz")
    assert datasource_mount.get_mounted_connection() is None


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_csv_mount_creates_view(tmp_path: Path) -> None:
    csv = tmp_path / "sales_orders.csv"
    csv.write_text("order_id,amount\n1,10.5\n2,20.0\n")
    _mount(str(csv))

    connection = datasource_mount.get_mounted_connection()
    assert connection is not None
    rows = connection.execute("SELECT COUNT(*) FROM sales_orders").fetchall()
    assert rows == [(2,)]


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_sql_script_mount_and_default_engine(tmp_path: Path) -> None:
    seed = tmp_path / "erp_seed.sql"
    seed.write_text(ERP_SEED)
    _mount(str(seed))

    import marimo as mo

    df = mo.sql(
        "SELECT po.po_number, gr.grv_number, si.invoice_number "
        "FROM purchase_orders po "
        "JOIN goods_received gr USING (po_number) "
        "JOIN sales_invoices si USING (grv_number)",
        output=False,
    )
    assert len(df) == 1


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_schema_context_includes_tables_and_fks(tmp_path: Path) -> None:
    seed = tmp_path / "erp_seed.sql"
    seed.write_text(ERP_SEED)
    _mount(str(seed))

    section = schema_context.get_mounted_schema_section()
    assert "<mounted_data_source>" in section
    for table in ("purchase_orders", "goods_received", "sales_invoices"):
        assert table in section
    assert "FOREIGN KEY" in section


@pytest.mark.skipif(not HAS_SQLALCHEMY, reason="sqlalchemy not installed")
def test_sqlalchemy_uri_mount_and_schema(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "erp.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE purchase_orders (po_number TEXT PRIMARY KEY);
        CREATE TABLE goods_received (
          grv_number TEXT PRIMARY KEY,
          po_number TEXT REFERENCES purchase_orders(po_number)
        );
        """
    )
    con.commit()
    con.close()
    _mount(f"sqlite:///{db.as_posix()}")

    section = schema_context.get_mounted_schema_section()
    assert "goods_received(po_number) -> " in section
    assert "purchase_orders(po_number)" in section


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_chat_system_prompt_contains_schema(tmp_path: Path) -> None:
    seed = tmp_path / "erp_seed.sql"
    seed.write_text(ERP_SEED)
    _mount(str(seed))

    from marimo._server.ai.prompts import get_chat_system_prompt
    from marimo._types.ids import SessionId

    prompt = get_chat_system_prompt(
        custom_rules=None,
        include_other_code="",
        mode="ask",
        session_id=SessionId("s1"),
    )
    assert "<mounted_data_source>" in prompt
    assert "sales_invoices" in prompt


def test_chat_system_prompt_unchanged_without_mount() -> None:
    from marimo._server.ai.prompts import get_chat_system_prompt
    from marimo._types.ids import SessionId

    prompt = get_chat_system_prompt(
        custom_rules=None,
        include_other_code="",
        mode="ask",
        session_id=SessionId("s1"),
    )
    assert "<mounted_data_source>" not in prompt


MERMAID_ERD = """erDiagram
    purchase_orders ||--o{ goods_received : "po_number"
    goods_received ||--o{ sales_invoices : "grv_number"
"""


def test_erd_injected_into_prompt(tmp_path: Path) -> None:
    erd = tmp_path / "model.mmd"
    erd.write_text(MERMAID_ERD)
    schema_context.set_erd(str(erd))

    from marimo._server.ai.prompts import get_chat_system_prompt
    from marimo._types.ids import SessionId

    prompt = get_chat_system_prompt(
        custom_rules=None,
        include_other_code="",
        mode="ask",
        session_id=SessionId("s1"),
    )
    assert "<data_model_erd>" in prompt
    assert "```mermaid" in prompt
    assert 'purchase_orders ||--o{ goods_received : "po_number"' in prompt


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_erd_replaces_whole_schema_indexing(tmp_path: Path) -> None:
    seed = tmp_path / "erp_seed.sql"
    seed.write_text(ERP_SEED)
    _mount(str(seed))
    erd = tmp_path / "model.mmd"
    erd.write_text(MERMAID_ERD)
    schema_context.set_erd(str(erd))

    section = schema_context.get_mounted_schema_section()
    assert "<mounted_data_source>" in section
    assert "<data_model_erd>" in section
    # the ERD is the data model: no column-level schema dump ...
    assert "po_number VARCHAR PK" not in section
    # ... and the model is told to query the DB for details instead
    assert "information_schema" in section
    assert "dialect: duckdb" in section


def test_erd_and_schema_combined_with_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARIMO_ERD_REPLACES_SCHEMA", "0")
    seed = tmp_path / "erp_seed.sql"
    seed.write_text(ERP_SEED)
    _mount(str(seed))
    erd = tmp_path / "model.mmd"
    erd.write_text(MERMAID_ERD)
    schema_context.set_erd(str(erd))

    section = schema_context.get_mounted_schema_section()
    assert "po_number VARCHAR PK" in section
    assert "<data_model_erd>" in section


def test_erd_image_rejected_gracefully(tmp_path: Path) -> None:
    erd = tmp_path / "model.png"
    erd.write_bytes(b"\x89PNG\r\n\x1a\n")
    schema_context.set_erd(str(erd))
    assert schema_context.get_mounted_schema_section() == ""


def test_erd_missing_file_never_raises() -> None:
    schema_context.set_erd("no_such_file.mmd")
    assert schema_context.get_mounted_schema_section() == ""


def test_erd_edits_apply_without_cache_restart(tmp_path: Path) -> None:
    erd = tmp_path / "model.mmd"
    erd.write_text(MERMAID_ERD)
    schema_context.set_erd(str(erd))
    assert "goods_received" in schema_context.get_mounted_schema_section()

    erd.write_text("erDiagram\n    a ||--o{ b : renamed_edge\n")
    assert "renamed_edge" in schema_context.get_mounted_schema_section()


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
async def test_mount_on_instantiate_injects_global_and_broadcasts(
    tmp_path: Path, mocked_kernel: MockedKernel
) -> None:
    seed = tmp_path / "erp_seed.sql"
    seed.write_text(ERP_SEED)
    _mount(str(seed))

    await mocked_kernel.k.instantiate(_instantiate_request())

    variable = datasource_mount.DATA_SOURCE_VARIABLE
    assert variable in mocked_kernel.k.globals

    from marimo._messaging.notification import (
        DataSourceConnectionsNotification,
    )

    connection_ops = [
        op
        for op in mocked_kernel.stream.operations
        if isinstance(op, DataSourceConnectionsNotification)
    ]
    assert connection_ops, "expected a data-source-connections broadcast"
    names = [conn.name for op in connection_ops for conn in op.connections]
    assert variable in names
