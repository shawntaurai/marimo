# Fork maintenance (shawntaurai/marimo)

This is a customization fork of [marimo-team/marimo](https://github.com/marimo-team/marimo)
for an internal analytics tool (dashboards + scheduled reports).

## Fork features

### Session-level data source mounting

Bind a session to one primary data source at launch:

```bash
marimo edit report.py --data-source postgresql://user:pw@host/db
marimo edit report.py --data-source ./sales.csv
marimo run report.py  --data-source ./warehouse.duckdb
MARIMO_DATA_SOURCE=postgresql://... python report.py   # scripts/cron
```

Supported specs: any SQLAlchemy URI; `.csv/.tsv/.parquet/.json/.jsonl`
(DuckDB view named after the file); `.sql` (seeds an in-memory DuckDB);
`.db/.duckdb/.ddb`; `.sqlite/.sqlite3`.

The mounted connection is exposed as the `datasource` variable, appears in
the datasources panel, and is the default engine for `mo.sql(...)` when no
`engine=` is passed. Code: `marimo/_fork/datasource_mount.py`; hooks in
`marimo/_cli/cli.py` (edit/run), `Kernel.instantiate`, `marimo/_sql/sql.py`.

### Schema-aware AI chat (NL → SQL)

When a source is mounted, every AI chat prompt automatically includes an
introspected schema: tables, columns with types, primary keys, and
foreign-key relationships, plus instructions to answer data questions with
SQL against the mounted source. Introspection is cached 5 minutes; size is
capped via `MARIMO_SCHEMA_CONTEXT_MAX_CHARS` (default 12000). Code:
`marimo/_fork/schema_context.py`; hook in `marimo/_server/ai/prompts.py`
(`_common_chat_sections`).

### LLM configuration (no code changes — stock marimo)

`~/.config/marimo/marimo.toml` points both AI roles at local Ollama
(`[ai.ollama] base_url = "http://127.0.0.1:11434/v1"`):
`ai.models.chat_model` / `edit_model` = `ollama/qwen2.5-coder:7b`,
`ai.models.autocomplete_model` = `ollama/qwen2.5-coder:1.5b-base` with
`completion.copilot = "custom"` for inline (FIM) completion.

Fork tests: `tests/_fork/` (`python -m pytest tests/_fork -q`).

## Branch layout

| Branch              | Purpose                                                        |
| ------------------- | -------------------------------------------------------------- |
| `main`              | Pristine mirror of `upstream/main`. **Never commit here.**     |
| `shawntauraiBranch` | All custom work. Rebased onto upstream **release tags**.       |

Remotes: `origin` = shawntaurai/marimo, `upstream` = marimo-team/marimo.

## Why sync to release tags, not upstream/main

`registry.npmjs.org` is blocked on this machine, so the frontend cannot be
built locally. Instead, `marimo/_static` and `marimo/_lsp` are extracted from
the official PyPI wheel of the **matching version**. Syncing to an arbitrary
commit on `upstream/main` would leave the Python backend ahead of the
prebuilt frontend assets. Release tags guarantee a wheel exists.

Note: marimo cuts its release commit on a side branch, so a tag (e.g.
`0.23.13`) is often *not* an ancestor of `upstream/main` — it shares a parent
with a main-line commit. Rebasing onto the tag is still correct.

## Sync workflow (per upstream release)

```bash
# 1. Fetch upstream and its tags
git fetch upstream --tags

# 2. Update the pristine mirror
git checkout main
git merge --ff-only upstream/main
git push origin main

# 3. Rebase custom work onto the new release tag
git checkout shawntauraiBranch
git rebase 0.23.XX          # the new tag
# resolve conflicts if any; `git rerere` is enabled, so resolutions
# you make once are replayed automatically next time

# 4. Refresh prebuilt frontend assets from the matching PyPI wheel
python scripts/fork_refresh_static.py 0.23.XX

# 5. Reinstall (editable install usually survives, but metadata may change)
pip install -e . --no-deps

# 6. Push (rebase rewrites history, so force-with-lease)
git push origin shawntauraiBranch --force-with-lease
```

## Rebase vs merge: why rebase

The fork is a single-developer patch stack. Rebasing keeps every custom
change as a readable commit *on top of* upstream, which:

- makes `git log 0.23.XX..shawntauraiBranch` an exact inventory of our diff
  from stock marimo;
- surfaces conflicts commit-by-commit at the exact change that caused them;
- combined with `rerere.enabled=true`, means each conflict is resolved once
  and replayed on every future sync.

If the custom stack ever grows past ~30 commits or gains multiple
contributors, switch to periodic `git merge <tag>` instead (no history
rewrites, no force pushes) — but until then rebase is cleaner.

## Conflict-minimization rules for custom code

1. **Prefer new files over edits to upstream files.** Fork-specific modules
   live in new files (e.g. `marimo/_fork/`, this `FORK.md`); upstream can't
   conflict with files it doesn't have.
2. **Keep edits to upstream files surgical** — small hook points that call
   into fork-owned modules, not large rewrites in place.
3. **One concern per commit**, so a conflicting commit can be dropped or
   redone during rebase without touching the rest.
4. **Never edit `marimo/_static/`** — it is generated output, overwritten on
   every wheel refresh. Frontend source changes go in `frontend/src/` (they
   only take effect when built on a network where npmjs works) or in the
   no-build `custom_css` overlay (`vscode-theme.css`).

## Git config already set (local repo)

- `rerere.enabled=true` — records and replays conflict resolutions
- `rebase.autoStash=true` — no need to stash dirty tree before rebasing
- `rebase.updateRefs=true` — keeps stacked branch refs in sync
