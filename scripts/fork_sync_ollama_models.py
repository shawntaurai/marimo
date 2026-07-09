# /// script
# requires-python = ">=3.11"
# dependencies = ["tomlkit"]
# ///
"""Sync marimo's AI model picker with the models installed in Ollama.

Fork helper (see FORK.md). marimo's model dropdown is fed by the static
`ai.models.custom_models` / `displayed_models` lists in marimo.toml — it
does not query Ollama. Run this after `ollama pull <model>` and every
installed model becomes selectable in the chat panel and AI settings:

    ollama pull mistral:7b
    python scripts/fork_sync_ollama_models.py
    # refresh the browser tab; pick the model in the chat panel

Queries the local Ollama daemon plus any `[ai.custom_providers.*]` whose
base_url looks like an Ollama server (port 11434). Unreachable providers
are skipped. Entries for other providers (openai/..., anthropic/...) are
left untouched; stale entries from reachable Ollama servers are pruned.
"""
# ruff: noqa: T201

from __future__ import annotations

import json
import urllib.error
import urllib.request

LOCAL_OLLAMA = "http://127.0.0.1:11434/v1"


def list_ollama_models(base_url: str) -> list[str] | None:
    """Model names from an Ollama server, or None if unreachable."""
    tags_url = base_url.removesuffix("/v1").rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as resp:
            payload = json.load(resp)
        # embedding models can't chat; keep them out of the picker
        return [
            m["name"]
            for m in payload.get("models", [])
            if "embed" not in m["name"].lower()
        ]
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  (skipped, unreachable: {tags_url}: {e})")
        return None


def main() -> None:
    import tomlkit

    from marimo._config.utils import get_or_create_user_config_path

    config_path = get_or_create_user_config_path()
    doc = tomlkit.parse(open(config_path, encoding="utf-8").read())

    ai = doc.setdefault("ai", tomlkit.table())
    models = ai.setdefault("models", tomlkit.table())

    # provider key -> base_url of every Ollama-flavored provider
    providers: dict[str, str] = {}
    ollama_cfg = ai.get("ollama", {})
    providers["ollama"] = ollama_cfg.get("base_url") or LOCAL_OLLAMA
    for key, cfg in (ai.get("custom_providers") or {}).items():
        base_url = cfg.get("base_url", "")
        if ":11434" in base_url:
            providers[key] = base_url

    fresh: list[str] = []
    reachable_prefixes: list[str] = []
    for key, base_url in providers.items():
        print(f"Querying {key} ({base_url}) ...")
        names = list_ollama_models(base_url)
        if names is None:
            continue
        reachable_prefixes.append(f"{key}/")
        fresh.extend(f"{key}/{name}" for name in names)
        for name in names:
            print(f"  found {key}/{name}")

    for list_key in ("custom_models", "displayed_models"):
        existing = list(models.get(list_key, []))
        # keep entries from other/unreachable providers, replace the rest
        kept = [
            m
            for m in existing
            if not any(m.startswith(p) for p in reachable_prefixes)
        ]
        models[list_key] = kept + [m for m in fresh if m not in kept]

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(tomlkit.dumps(doc))
    print(f"\nUpdated {config_path}")
    print(
        "Default models (ai.models.chat_model / edit_model / "
        "autocomplete_model) are unchanged; switch per-conversation in the "
        "chat panel's model picker, or edit the config to change defaults."
    )


if __name__ == "__main__":
    main()
