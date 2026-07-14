# Copyright 2026 Marimo. All rights reserved.
"""Self-update for the dedomena fork (see FORK.md).

Exposes the FORK.md sync workflow as an API so the home page can show an
"Update" button when upstream marimo publishes a new release:

1. `git fetch upstream --tags`
2. `git rebase <new release tag>` — fork commits replay on top, so all
   fork features are preserved; conflicts abort cleanly and ask for a
   manual sync (rerere replays previously-seen resolutions automatically)
3. `scripts/fork_refresh_static.py` — matching frontend assets from the
   upstream PyPI wheel, then dedomena re-branding
4. `git push origin --force-with-lease` (best-effort)

The running server keeps serving the old code from memory; a restart
loads the synced version. No pip reinstall is needed — the version is
read from the checkout's pyproject.toml (see marimo/_version.py).
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from marimo import _loggers
from marimo._version import __version__

LOGGER = _loggers.marimo_logger()

REPO_ROOT = Path(__file__).resolve().parents[2]

_LATEST_VERSION_URL = "https://marimo.io/api/oss/latest-version"
_LATEST_CACHE_TTL_SECONDS = 3600

_lock = threading.Lock()
_state: dict[str, Any] = {"sync_status": "idle", "detail": "", "log": []}
_latest_cache: tuple[float, Optional[str]] | None = None


def is_managed_checkout() -> bool:
    """True when running from the fork's git checkout (editable install)."""
    return (REPO_ROOT / ".git").exists() and (
        REPO_ROOT / "scripts" / "fork_refresh_static.py"
    ).exists()


def get_latest_upstream_version() -> Optional[str]:
    global _latest_cache
    if (
        _latest_cache is not None
        and time.time() - _latest_cache[0] < _LATEST_CACHE_TTL_SECONDS
    ):
        return _latest_cache[1]

    latest: Optional[str] = None
    try:
        from marimo._utils import requests

        response = requests.get(_LATEST_VERSION_URL, timeout=3)
        response.raise_for_status()
        latest = response.json()["info"]["version"]
    except Exception as e:
        LOGGER.warning("Failed to fetch latest upstream version: %s", e)
    _latest_cache = (time.time(), latest)
    return latest


def get_status() -> dict[str, Any]:
    latest = get_latest_upstream_version()
    update_available = False
    if latest:
        try:
            from packaging import version

            update_available = version.parse(latest) > version.parse(
                __version__
            )
        except Exception:
            update_available = latest != __version__
    with _lock:
        return {
            "managed": is_managed_checkout(),
            "current_version": __version__,
            "latest_version": latest,
            "update_available": update_available,
            "sync_status": _state["sync_status"],
            "detail": _state["detail"],
            "log": list(_state["log"]),
        }


def start_sync() -> bool:
    """Kick off a background sync. Returns False if one is already running."""
    with _lock:
        if _state["sync_status"] == "running":
            return False
        _state.update(sync_status="running", detail="Starting sync…", log=[])
    threading.Thread(target=_run_sync, daemon=True).start()
    return True


def _set(detail: str, status: Optional[str] = None) -> None:
    with _lock:
        _state["detail"] = detail
        _state["log"].append(detail)
        if status is not None:
            _state["sync_status"] = status
    LOGGER.info("fork updater: %s", detail)


def _git(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_sync() -> None:
    try:
        if not is_managed_checkout():
            _set("Not a managed git checkout; cannot self-update.", "error")
            return

        dirty = _git("status", "--porcelain", "-uno").stdout.strip()
        if dirty:
            _set(
                "Working tree has uncommitted changes — commit or stash "
                "them, then retry.",
                "error",
            )
            return

        _set("Fetching upstream releases…")
        fetch = _git("fetch", "upstream", "--tags")
        if fetch.returncode != 0:
            _set(f"git fetch failed: {fetch.stderr.strip()[:300]}", "error")
            return

        target = get_latest_upstream_version()
        if not target:
            _set("Could not determine the latest upstream version.", "error")
            return
        if _git("rev-parse", "--verify", f"refs/tags/{target}").returncode:
            _set(f"Upstream tag {target} not found after fetch.", "error")
            return

        _set(f"Rebasing fork commits onto {target}…")
        rebase = _git("rebase", target)
        if rebase.returncode != 0:
            _git("rebase", "--abort")
            _set(
                f"Rebase onto {target} hit conflicts and was aborted — "
                "nothing was changed. Run the manual sync in FORK.md to "
                "resolve them once (rerere remembers the resolution).",
                "error",
            )
            return

        _set(f"Downloading {target} frontend assets from PyPI…")
        refresh = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "fork_refresh_static.py"),
                target,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if refresh.returncode != 0:
            _set(
                "Asset refresh failed: "
                f"{(refresh.stderr or refresh.stdout).strip()[:300]}",
                "error",
            )
            return

        _set("Pushing synced branch…")
        push = _git("push", "origin", "HEAD", "--force-with-lease")
        if push.returncode != 0:
            _set(f"(push skipped: {push.stderr.strip()[:200]})")

        _set(
            f"Synced to {target} with all fork features preserved. "
            "Restart the server (Ctrl+C, then `dedomena edit`) to load it.",
            "done",
        )
    except Exception as e:
        LOGGER.error("fork updater failed", exc_info=e)
        _set(f"Update failed: {e}", "error")


# Injected into the home page (see templates.py). Kept dependency-free and
# defensive: any API failure means the button simply never appears.
UPDATE_BUTTON_SNIPPET = """
<script>
(function () {
  function token() {
    var el = document.querySelector("marimo-server-token");
    return el ? el.getAttribute("data-token") : "";
  }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign(
      { "Marimo-Server-Token": token() }, opts.headers || {});
    return fetch("api/fork/update/" + path, opts);
  }
  api("status").then(function (r) { return r.ok ? r.json() : null; })
    .then(function (s) {
      if (!s || !s.managed || !s.update_available) return;
      var b = document.createElement("button");
      b.id = "dedomena-update-btn";
      b.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:9999;" +
        "padding:10px 16px;border-radius:8px;border:none;background:#0f4c4c;" +
        "color:#fff;font:600 13px system-ui;cursor:pointer;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.25);max-width:340px;text-align:left";
      b.textContent = "\\u2B06 Update dedomena " + s.current_version +
        " \\u2192 " + s.latest_version;
      b.title = "Rebases your fork onto the new upstream release, " +
        "keeping all dedomena features";
      b.onclick = function () {
        b.disabled = true;
        b.textContent = "Updating\\u2026";
        api("start", { method: "POST" }).then(function () {
          var t = setInterval(function () {
            api("status").then(function (r) { return r.json(); })
              .then(function (s2) {
                b.textContent = s2.detail || "Updating\\u2026";
                if (s2.sync_status === "done") {
                  clearInterval(t); b.style.background = "#1a7f37";
                } else if (s2.sync_status === "error") {
                  clearInterval(t); b.style.background = "#b42318";
                  b.disabled = false;
                }
              }).catch(function () {});
          }, 2000);
        });
      };
      document.body.appendChild(b);
    }).catch(function () {});
})();
</script>
"""


def inject_update_button(html: str) -> str:
    """Append the update-button script to a served home page."""
    if "</body>" in html:
        return html.replace("</body>", UPDATE_BUTTON_SNIPPET + "</body>", 1)
    return html + UPDATE_BUTTON_SNIPPET
