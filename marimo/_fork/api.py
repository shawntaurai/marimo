# Copyright 2026 Marimo. All rights reserved.
"""Fork API endpoints (see FORK.md), mounted under /api/fork."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from starlette.authentication import requires
from starlette.responses import JSONResponse

from marimo._fork import updater
from marimo._server.router import APIRouter

if TYPE_CHECKING:
    from starlette.requests import Request

router = APIRouter()


@router.get("/update/status")
@requires("edit")
async def update_status(request: Request) -> JSONResponse:
    del request
    return JSONResponse(updater.get_status())


@router.post("/update/start")
@requires("edit")
async def update_start(request: Request) -> JSONResponse:
    del request
    started = updater.start_sync()
    return JSONResponse({"started": started})


def _mask_secret(uri: str) -> str:
    return re.sub(r"(://[^:/@]+):[^@]+@", r"\1:****@", uri)


@router.get("/mount")
@requires("edit")
async def mount_status(request: Request) -> JSONResponse:
    del request
    import os

    from marimo._fork import erd as erd_mod
    from marimo._fork.datasource_mount import DATA_SOURCE_ENV_VAR

    result: dict[str, Any] = {
        "data_source": _mask_secret(os.environ.get(DATA_SOURCE_ENV_VAR, "")),
        "erd": erd_mod.get_erd_path() or "",
    }
    index = erd_mod.load_erd()
    if index is not None and index.is_graph:
        result["erd_summary"] = (
            f"{len(index.tables)} tables, "
            f"{len(index.relationships)} relationships"
        )
    return JSONResponse(result)


@router.post("/mount")
@requires("edit")
async def mount_set(request: Request) -> JSONResponse:
    from marimo._fork import datasource_mount, erd as erd_mod, schema_context

    body = await request.json()
    result: dict[str, Any] = {}

    data_source = (body.get("data_source") or "").strip()
    if data_source and "****" not in data_source:
        result["data_source_ok"] = datasource_mount.remount(data_source)
        schema_context._cache = None

    erd_path = (body.get("erd") or "").strip()
    if erd_path:
        index = erd_mod.reload_erd(erd_path)
        result["erd_ok"] = index is not None
        if index is not None and index.is_graph:
            result["erd_summary"] = (
                f"{len(index.tables)} tables, "
                f"{len(index.relationships)} relationships"
            )
        schema_context._cache = None

    return JSONResponse(result)
