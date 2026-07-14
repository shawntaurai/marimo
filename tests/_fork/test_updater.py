# Copyright 2026 Marimo. All rights reserved.
"""Tests for the fork self-updater (update button backend)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from marimo._fork import updater
from marimo._version import __version__

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def reset_updater() -> Generator[None, None, None]:
    updater._latest_cache = None
    with updater._lock:
        updater._state.update(sync_status="idle", detail="", log=[])
    yield
    updater._latest_cache = None
    with updater._lock:
        updater._state.update(sync_status="idle", detail="", log=[])


def test_is_managed_checkout() -> None:
    # tests run from the fork checkout, so this must be True
    assert updater.is_managed_checkout()


def test_status_update_available() -> None:
    updater._latest_cache = (time.time(), "999.0.0")
    status = updater.get_status()
    assert status["managed"] is True
    assert status["current_version"] == __version__
    assert status["latest_version"] == "999.0.0"
    assert status["update_available"] is True
    assert status["sync_status"] == "idle"


def test_status_no_update_when_current() -> None:
    updater._latest_cache = (time.time(), __version__)
    assert updater.get_status()["update_available"] is False


def test_status_survives_network_failure() -> None:
    updater._latest_cache = (time.time(), None)
    status = updater.get_status()
    assert status["latest_version"] is None
    assert status["update_available"] is False


def test_start_sync_guards_against_double_start() -> None:
    with updater._lock:
        updater._state["sync_status"] = "running"
    assert updater.start_sync() is False


def test_inject_update_button() -> None:
    html = "<html><body><div>app</div></body></html>"
    injected = updater.inject_update_button(html)
    assert "dedomena-update-btn" in injected
    assert injected.index("dedomena-update-btn") < injected.index("</body>")
    # idempotent-ish: no body tag still appends
    assert "dedomena-update-btn" in updater.inject_update_button("<div/>")
