# Copyright 2026 Marimo. All rights reserved.
"""Floating data-source mount panel injected into served pages (fork).

Lets the user bind/change the session data source and ERD from the
browser instead of CLI flags: a small 🗄 button (bottom-left) opens a
panel with two fields (database URI, ERD file path on the server
machine) that POST to /api/fork/mount.

The change applies to the server immediately (AI chat context) and to
any kernel started afterwards; already-running kernels keep their
existing connection until restarted.
"""

from __future__ import annotations

MOUNT_PANEL_SNIPPET = """
<script>
(function () {
  function token() {
    var el = document.querySelector("marimo-server-token");
    return el ? el.getAttribute("data-token") : "";
  }
  function api(opts) {
    opts = opts || {};
    opts.headers = Object.assign(
      { "Marimo-Server-Token": token(), "Content-Type": "application/json" },
      opts.headers || {});
    return fetch(document.baseURI.replace(/[^/]*$/, "") +
      "api/fork/mount", opts);
  }
  var btn = document.createElement("button");
  btn.id = "dedomena-mount-btn";
  btn.textContent = "\\uD83D\\uDDC4";
  btn.title = "dedomena: mount data source / ERD";
  btn.style.cssText = "position:fixed;bottom:16px;left:16px;z-index:9999;" +
    "width:38px;height:38px;border-radius:50%;border:none;cursor:pointer;" +
    "background:#0f4c4c;color:#fff;font-size:17px;" +
    "box-shadow:0 2px 8px rgba(0,0,0,.25)";
  var panel = null;
  function buildPanel(state) {
    panel = document.createElement("div");
    panel.id = "dedomena-mount-panel";
    panel.style.cssText = "position:fixed;bottom:62px;left:16px;z-index:9999;" +
      "width:340px;padding:14px;border-radius:10px;background:#0f4c4c;" +
      "color:#fff;font:13px system-ui;box-shadow:0 4px 16px rgba(0,0,0,.35)";
    panel.innerHTML =
      "<div style='font-weight:600;margin-bottom:8px'>Data source</div>" +
      "<input id='dm-ds' placeholder='postgresql://user:pw@host:5432/db " +
      "or C:\\\\path\\\\data.csv' style='width:100%;margin-bottom:8px;" +
      "padding:6px;border-radius:6px;border:none;font:12px monospace;" +
      "color:#111'>" +
      "<div style='font-weight:600;margin-bottom:8px'>ERD file " +
      "(.pgerd/.mmd/.dbml/... on this machine)</div>" +
      "<input id='dm-erd' placeholder='C:\\\\path\\\\model.pgerd' " +
      "style='width:100%;margin-bottom:10px;padding:6px;border-radius:6px;" +
      "border:none;font:12px monospace;color:#111'>" +
      "<button id='dm-apply' style='padding:7px 14px;border:none;" +
      "border-radius:6px;background:#fff;color:#0f4c4c;font-weight:600;" +
      "cursor:pointer'>Apply</button>" +
      "<div id='dm-status' style='margin-top:8px;min-height:16px;" +
      "font-size:12px;opacity:.9'></div>";
    document.body.appendChild(panel);
    if (state) {
      // the server masks credentials; show current value as a placeholder
      // only - re-sending the masked string must never overwrite the mount
      if (state.data_source)
        panel.querySelector("#dm-ds").placeholder =
          "connected: " + state.data_source + " (leave empty to keep)";
      if (state.erd) panel.querySelector("#dm-erd").value = state.erd;
      if (state.erd_summary)
        panel.querySelector("#dm-status").textContent = "ERD: " + state.erd_summary;
    }
    panel.querySelector("#dm-apply").onclick = function () {
      var status = panel.querySelector("#dm-status");
      status.textContent = "Applying\\u2026";
      api({ method: "POST", body: JSON.stringify({
        data_source: panel.querySelector("#dm-ds").value,
        erd: panel.querySelector("#dm-erd").value,
      })}).then(function (r) { return r.json(); }).then(function (res) {
        var parts = [];
        if ("data_source_ok" in res) {
          parts.push(res.data_source_ok ? "\\u2713 data source connected"
                                        : "\\u2717 data source failed");
          if (res.data_source_ok) {
            // don't leave credentials visible on screen
            var ds = panel.querySelector("#dm-ds");
            ds.value = "";
            ds.placeholder = "connected (leave empty to keep)";
          }
        }
        if ("erd_ok" in res)
          parts.push(res.erd_ok
            ? "\\u2713 ERD loaded" + (res.erd_summary ? " (" + res.erd_summary + ")" : "")
            : "\\u2717 ERD failed (text formats or .pgerd only)");
        parts.push("AI chat uses this now; restart the notebook kernel " +
          "to expose the `datasource` variable in cells.");
        status.textContent = parts.join(" \\u00B7 ");
      }).catch(function (e) { status.textContent = "Failed: " + e; });
    };
  }
  btn.onclick = function () {
    if (panel) { panel.remove(); panel = null; return; }
    api().then(function (r) { return r.ok ? r.json() : null; })
      .then(buildPanel).catch(function () { buildPanel(null); });
  };
  document.body.appendChild(btn);
})();
</script>
"""


def inject_mount_panel(html: str) -> str:
    """Append the mount-panel script to a served page."""
    if "</body>" in html:
        return html.replace("</body>", MOUNT_PANEL_SNIPPET + "</body>", 1)
    return html + MOUNT_PANEL_SNIPPET
