# Copyright 2026 Marimo. All rights reserved.
"""User-supplied ERD parsing and lookup (fork, see FORK.md).

Supports two kinds of ERD files supplied via `--erd` / the UI mount panel:

- **Text formats** (Mermaid, DBML, PlantUML, markdown, SQL DDL): included
  verbatim in the AI prompt when they fit the size budget.
- **pgAdmin `.pgerd` exports**: JSON documents that can describe an entire
  ERP (hundreds of tables, thousands of relationships). These are parsed
  into an in-memory index. Small ones are rendered into the prompt as
  relationship lines; large ones are summarized, and the model looks up
  the parts it needs through the `get_erd_relationships` AI tool
  (agent mode) instead of drowning its context.

The index is cached by file path + mtime, so saving a new export from
pgAdmin applies to the next chat message without a restart.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from marimo import _loggers

LOGGER = _loggers.marimo_logger()

ERD_ENV_VAR = "MARIMO_DATA_SOURCE_ERD"

TEXT_SUFFIXES = {
    ".mmd",
    ".mermaid",
    ".dbml",
    ".puml",
    ".plantuml",
    ".md",
    ".markdown",
    ".txt",
    ".sql",
    ".json",
    ".yaml",
    ".yml",
}
PGERD_SUFFIX = ".pgerd"
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {PGERD_SUFFIX}


@dataclass
class Relationship:
    local_table: str
    local_column: str
    referenced_table: str
    referenced_column: str

    def render(self) -> str:
        return (
            f"{self.local_table}.{self.local_column} -> "
            f"{self.referenced_table}.{self.referenced_column}"
        )


@dataclass
class ErdIndex:
    """Parsed ERD: raw text for small files, a queryable graph for pgerd."""

    raw_text: Optional[str] = None
    fence: str = "text"
    relationships: list[Relationship] = field(default_factory=list)
    # table name -> column names (pgerd carries these; text formats do not)
    tables: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_graph(self) -> bool:
        return bool(self.relationships or self.tables)

    def search(
        self, keywords: list[str], max_relationships: int = 200
    ) -> tuple[list[str], list[Relationship]]:
        """Tables and relationships matching any keyword (substring)."""
        lowered = [k.strip().lower() for k in keywords if k.strip()]
        if not lowered:
            return [], []
        matched_tables = [
            t for t in self.tables if any(k in t.lower() for k in lowered)
        ]
        matched_set = set(matched_tables)
        rels = [
            r
            for r in self.relationships
            if r.local_table in matched_set
            or r.referenced_table in matched_set
            or any(
                k in r.local_table.lower() or k in r.referenced_table.lower()
                for k in lowered
            )
        ]
        return matched_tables, rels[:max_relationships]


_cache: tuple[str, float, Optional[ErdIndex]] | None = None


def set_erd(path: str) -> None:
    """Record the ERD path so child (kernel) processes inherit it."""
    os.environ[ERD_ENV_VAR] = path


def get_erd_path() -> Optional[str]:
    spec = os.environ.get(ERD_ENV_VAR, "").strip()
    return spec or None


def reload_erd(path: str) -> Optional[ErdIndex]:
    """Point the process at a new ERD file and parse it now."""
    global _cache
    set_erd(path)
    _cache = None
    return load_erd()


def load_erd() -> Optional[ErdIndex]:
    """Load (cached by path+mtime) the configured ERD. Never raises."""
    global _cache
    spec = get_erd_path()
    if not spec:
        return None
    try:
        path = Path(spec)
        if not path.exists():
            LOGGER.error("ERD file not found: %s", spec)
            return None
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            LOGGER.error(
                "ERD file %s is not a supported format %s. Images cannot "
                "be read by the SQL model - export the diagram as pgAdmin "
                ".pgerd, Mermaid (.mmd), DBML, PlantUML, markdown, or SQL "
                "DDL.",
                spec,
                sorted(SUPPORTED_SUFFIXES),
            )
            return None

        mtime = path.stat().st_mtime
        if _cache is not None and _cache[0] == spec and _cache[1] == mtime:
            return _cache[2]

        if suffix == PGERD_SUFFIX:
            index = _parse_pgerd(path)
        else:
            fence = (
                "mermaid"
                if suffix in (".mmd", ".mermaid")
                else suffix.lstrip(".")
            )
            index = ErdIndex(
                raw_text=path.read_text(
                    encoding="utf-8", errors="replace"
                ).strip(),
                fence=fence,
            )
        _cache = (spec, mtime, index)
        return index
    except Exception as e:
        LOGGER.warning("Failed to load ERD file %s: %s", spec, e, exc_info=e)
        return None


def _parse_pgerd(path: Path) -> ErdIndex:
    doc = json.loads(path.read_text(encoding="utf-8"))
    nodes: dict[str, dict[int, str]] = {}  # uid -> {attnum: column}
    names: dict[str, str] = {}  # uid -> table name
    tables: dict[str, list[str]] = {}
    links: list[dict[str, object]] = []

    for layer in doc["data"]["layers"]:
        models = layer.get("models", {})
        layer_type = layer.get("type")
        if layer_type == "diagram-nodes":
            for uid, model in models.items():
                data = model["otherInfo"]["data"]
                name = data["name"]
                columns = data.get("columns", [])
                names[uid] = name
                nodes[uid] = {c["attnum"]: c["name"] for c in columns}
                tables[name] = [c["name"] for c in columns]
        elif layer_type == "diagram-links":
            for model in models.values():
                if model.get("data"):
                    links.append(model["data"])

    relationships = []
    for link in links:
        try:
            local_uid = str(link["local_table_uid"])
            ref_uid = str(link["referenced_table_uid"])
            relationships.append(
                Relationship(
                    local_table=names[local_uid],
                    local_column=nodes[local_uid][
                        int(link["local_column_attnum"])  # type: ignore[arg-type]
                    ],
                    referenced_table=names[ref_uid],
                    referenced_column=nodes[ref_uid][
                        int(link["referenced_column_attnum"])  # type: ignore[arg-type]
                    ],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    LOGGER.info(
        "Parsed pgAdmin ERD %s: %d tables, %d relationships",
        path.name,
        len(tables),
        len(relationships),
    )
    return ErdIndex(relationships=relationships, tables=tables)


def render_for_prompt(index: ErdIndex, max_chars: int) -> str:
    """Body of the ERD prompt section, bounded by max_chars."""
    if index.raw_text is not None:
        text = index.raw_text
        truncated = ""
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = "\n... (ERD truncated)"
        return f"```{index.fence}\n{text}\n```{truncated}"

    lines = [r.render() for r in index.relationships]
    body = "\n".join(lines)
    if len(body) <= max_chars:
        return (
            f"Relationships ({len(lines)}), as "
            f"`local_table.column -> referenced_table.column`:\n" + body
        )

    # Too large to inline: ground the model with the real table inventory
    # and defer relationship details to the lookup tool.
    header = (
        f"The ERD describes {len(index.tables)} tables and "
        f"{len(index.relationships)} relationships - too many to list "
        "in full.\n"
        "- NEVER invent, abbreviate, or shorten table or column names. "
        "Only use tables from the inventory below, copied verbatim "
        "(never drop prefixes like c_ / m_ / ad_), and get their exact "
        "columns and join paths "
        "first: call the `get_erd_relationships` tool with keywords "
        "(e.g. ['invoice', 'bpartner']), or query "
        "information_schema.columns when the tool is unavailable.\n\n"
        "Table inventory (exact names):\n"
    )
    inventory = _table_inventory(
        index.tables, budget=max(1000, max_chars - len(header))
    )
    return header + inventory


# Words too generic to identify tables/columns
_STOPWORDS = {
    "give",
    "show",
    "get",
    "list",
    "find",
    "all",
    "the",
    "and",
    "for",
    "from",
    "with",
    "that",
    "this",
    "these",
    "those",
    "which",
    "what",
    "who",
    "how",
    "many",
    "much",
    "between",
    "during",
    "within",
    "each",
    "per",
    "top",
    "highest",
    "lowest",
    "total",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}


def focused_context(
    question: str, max_tables: int = 8, max_chars: int = 5000
) -> str:
    """ERD excerpt relevant to a question: tables, columns, join paths.

    Matches question words against table AND column names (business terms
    often surface in column names, e.g. "sales" -> `salesrep_id`), then
    renders the best-matching tables' column lists and the join paths
    among them. Returns "" when nothing matches or no graph ERD is loaded.
    """
    import re

    index = load_erd()
    if index is None or not index.is_graph:
        return ""

    tokens = {
        w.rstrip("s")
        for w in re.findall(r"[a-zA-Z]{3,}", question.lower())
        if w not in _STOPWORDS
    }
    tokens = {t for t in tokens if len(t) >= 3}
    if not tokens:
        return ""

    # connectivity: transactional core tables are relationship hubs,
    # while lookalike report/summary tables are leaves
    degree: dict[str, int] = {}
    for r in index.relationships:
        degree[r.local_table] = degree.get(r.local_table, 0) + 1
        degree[r.referenced_table] = degree.get(r.referenced_table, 0) + 1

    scores: dict[str, float] = {}
    for table, columns in index.tables.items():
        name = table.lower()
        column_text = " ".join(columns).lower()
        name_hits = sum(1 for t in tokens if t in name)
        column_hits = sum(column_text.count(t) for t in tokens)
        if name_hits or column_hits:
            import math

            scores[table] = (
                5.0 * name_hits
                + min(column_hits, 5)
                + math.log1p(degree.get(table, 0))
            )
    if not scores:
        return ""

    selected = [
        t
        for t, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:max_tables]
    ]
    selected_set = set(selected)

    lines = ["Tables most relevant to this question (from the ERD):"]
    for table in selected:
        columns = index.tables.get(table, [])
        shown = ", ".join(columns[:40])
        more = f", ... +{len(columns) - 40} more" if len(columns) > 40 else ""
        lines.append(f"- {table}({shown}{more})")

    joins = [
        r.render()
        for r in index.relationships
        if r.local_table in selected_set and r.referenced_table in selected_set
    ]
    if joins:
        lines.append("Join paths among them:")
        lines.extend(f"- {j}" for j in joins[:30])

    text = "\n".join(lines)
    return text[:max_chars]


def _table_inventory(tables: dict[str, list[str]], budget: int) -> str:
    text = ", ".join(sorted(tables))
    if len(text) > budget:
        text = text[:budget].rsplit(",", 1)[0]
        text += "\n... (inventory truncated; more tables exist - use the tool)"
    return text
