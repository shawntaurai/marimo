# Copyright 2026 Marimo. All rights reserved.
"""Tests for ERD parsing (.pgerd), lookup tool, and mount endpoints."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from marimo._fork import erd
from marimo._fork.ai_tools import (
    GetErdRelationships,
    GetErdRelationshipsArgs,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def reset_erd(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile
    from pathlib import Path

    from marimo._fork import datasource_mount

    # never read or write the developer's real persisted mount in tests
    persist_dir = tempfile.mkdtemp()
    monkeypatch.setattr(
        datasource_mount,
        "_persist_file",
        lambda: Path(persist_dir) / "mount.json",
    )
    erd._cache = None
    os.environ.pop(erd.ERD_ENV_VAR, None)
    yield
    erd._cache = None
    os.environ.pop(erd.ERD_ENV_VAR, None)


def _pgerd_doc() -> dict:
    """Minimal pgAdmin ERD: orders -> partners via partner_id."""

    def node(uid: str, name: str, columns: list[str]) -> dict:
        return {
            "id": uid,
            "type": "table",
            "otherInfo": {
                "data": {
                    "name": name,
                    "schema": "public",
                    "columns": [
                        {"name": c, "attnum": i + 1, "cltype": "text"}
                        for i, c in enumerate(columns)
                    ],
                }
            },
        }

    return {
        "version": 1,
        "data": {
            "layers": [
                {
                    "type": "diagram-nodes",
                    "models": {
                        "uid-partners": node(
                            "uid-partners", "partners", ["partner_id", "name"]
                        ),
                        "uid-orders": node(
                            "uid-orders",
                            "orders",
                            ["order_id", "partner_id", "total"],
                        ),
                    },
                },
                {
                    "type": "diagram-links",
                    "models": {
                        "l1": {
                            "data": {
                                "local_table_uid": "uid-orders",
                                "local_column_attnum": 2,
                                "referenced_table_uid": "uid-partners",
                                "referenced_column_attnum": 1,
                            }
                        }
                    },
                },
            ]
        },
    }


def _write_pgerd(tmp_path: Path) -> Path:
    path = tmp_path / "model.pgerd"
    path.write_text(json.dumps(_pgerd_doc()), encoding="utf-8")
    return path


def test_pgerd_parses_into_graph(tmp_path: Path) -> None:
    index = erd.reload_erd(str(_write_pgerd(tmp_path)))
    assert index is not None
    assert index.is_graph
    assert index.tables["orders"] == ["order_id", "partner_id", "total"]
    assert [r.render() for r in index.relationships] == [
        "orders.partner_id -> partners.partner_id"
    ]


def test_small_pgerd_renders_inline(tmp_path: Path) -> None:
    index = erd.reload_erd(str(_write_pgerd(tmp_path)))
    body = erd.render_for_prompt(index, max_chars=8000)
    assert "orders.partner_id -> partners.partner_id" in body


def test_large_pgerd_defers_to_tool_with_inventory(tmp_path: Path) -> None:
    index = erd.reload_erd(str(_write_pgerd(tmp_path)))
    body = erd.render_for_prompt(index, max_chars=10)
    assert "get_erd_relationships" in body
    assert "NEVER invent, abbreviate, or shorten table" in body
    # real table names are listed so the model can't hallucinate others
    assert "orders" in body


def test_cell_generation_prompt_includes_mounted_context(
    tmp_path: Path,
) -> None:
    from marimo._fork import datasource_mount, schema_context

    erd_file = tmp_path / "model.pgerd"
    erd_file.write_text(json.dumps(_pgerd_doc()), encoding="utf-8")
    erd.reload_erd(str(erd_file))
    saved = os.environ.pop(datasource_mount.DATA_SOURCE_ENV_VAR, None)
    schema_context._cache = None
    try:
        from marimo._server.ai.prompts import (
            get_refactor_or_insert_notebook_cell_system_prompt,
        )

        prompt = get_refactor_or_insert_notebook_cell_system_prompt(
            language="python",
            is_insert=False,
            support_multiple_cells=True,
            custom_rules=None,
            cell_code=None,
            selected_text=None,
            other_cell_codes=None,
            context=None,
        )
        assert "<data_model_erd>" in prompt
    finally:
        schema_context._cache = None
        if saved is not None:
            os.environ[datasource_mount.DATA_SOURCE_ENV_VAR] = saved


def test_lookup_tool_finds_relationships(tmp_path: Path) -> None:
    erd.reload_erd(str(_write_pgerd(tmp_path)))
    tool = GetErdRelationships.__new__(GetErdRelationships)
    out = tool.handle(GetErdRelationshipsArgs(keywords=["order"]))
    assert "orders.partner_id -> partners.partner_id" in out.relationships
    assert out.table_columns["orders"] == ["order_id", "partner_id", "total"]


def test_lookup_tool_without_erd() -> None:
    tool = GetErdRelationships.__new__(GetErdRelationships)
    out = tool.handle(GetErdRelationshipsArgs(keywords=["order"]))
    assert out.status == "error"


def test_erd_cache_busts_on_mtime(tmp_path: Path) -> None:
    path = _write_pgerd(tmp_path)
    index = erd.reload_erd(str(path))
    assert index is not None
    doc = _pgerd_doc()
    doc["data"]["layers"][0]["models"]["uid-extra"] = {
        "id": "uid-extra",
        "type": "table",
        "otherInfo": {
            "data": {"name": "extra", "schema": "public", "columns": []}
        },
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    index2 = erd.load_erd()
    assert "extra" in index2.tables


def test_mount_settings_persist() -> None:
    from marimo._fork import datasource_mount

    datasource_mount.persist_mount(data_source="sqlite:///x.db")
    datasource_mount.persist_mount(erd="C:/erd/model.pgerd")
    saved = datasource_mount.load_persisted_mount()
    assert saved == {
        "data_source": "sqlite:///x.db",
        "erd": "C:/erd/model.pgerd",
    }


def test_mask_secret() -> None:
    from marimo._fork.api import _mask_secret

    masked = _mask_secret("postgresql://postgres:tbJbC5%40%40vM@host:5432/db")
    assert "tbJbC5" not in masked
    assert masked == "postgresql://postgres:****@host:5432/db"


def test_sql_correction_suggests_column(tmp_path: Path) -> None:
    from marimo._fork import sql_correction

    erd.reload_erd(str(_write_pgerd(tmp_path)))
    hint = sql_correction.suggest_from_erd(
        'column "partnre_id" does not exist'
    )
    assert hint is not None
    assert "partner_id" in hint


def test_sql_correction_suggests_table(tmp_path: Path) -> None:
    from marimo._fork import sql_correction

    erd.reload_erd(str(_write_pgerd(tmp_path)))
    hint = sql_correction.suggest_from_erd('relation "order" does not exist')
    assert hint is not None
    assert "orders" in hint


def test_sql_correction_none_without_match(tmp_path: Path) -> None:
    from marimo._fork import sql_correction

    erd.reload_erd(str(_write_pgerd(tmp_path)))
    assert sql_correction.suggest_from_erd("unrelated error") is None


def test_sql_correction_none_without_erd() -> None:
    from marimo._fork import sql_correction

    assert sql_correction.suggest_from_erd('column "x" does not exist') is None


def test_sql_correction_scopes_to_qualified_table(tmp_path: Path) -> None:
    from marimo._fork import sql_correction

    erd.reload_erd(str(_write_pgerd(tmp_path)))
    # 'orders' has partner_id; a qualified miss should prefer that table
    hint = sql_correction.suggest_from_erd(
        'column "orders.partner_di" does not exist'
    )
    assert hint is not None
    assert "partner_id (on orders)" in hint
