# Copyright 2026 Marimo. All rights reserved.
"""Fork API endpoints (see FORK.md), mounted under /api/fork."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.authentication import requires
from starlette.responses import JSONResponse

from marimo._fork import updater
from marimo._server.router import APIRouter

if TYPE_CHECKING:
    from starlette.requests import Request

router = APIRouter()


@router.get("/status")
@requires("edit")
async def update_status(request: Request) -> JSONResponse:
    del request
    return JSONResponse(updater.get_status())


@router.post("/start")
@requires("edit")
async def update_start(request: Request) -> JSONResponse:
    del request
    started = updater.start_sync()
    return JSONResponse({"started": started})
