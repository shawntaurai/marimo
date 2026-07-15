# Copyright 2026 Marimo. All rights reserved.
"""Fork AI tools (see FORK.md).

`get_erd_relationships` lets agent-mode chat look up how tables relate in
the user's ERD (typically a pgAdmin `.pgerd` export of an ERP with
thousands of relationships — far too many to inline in the prompt).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from marimo import _loggers
from marimo._ai._tools.base import ToolBase
from marimo._ai._tools.types import SuccessResult, ToolGuidelines

LOGGER = _loggers.marimo_logger()


@dataclass
class GetErdRelationshipsArgs:
    keywords: list[str]
    """Table names or name fragments, e.g. ["invoice", "bpartner"]."""


@dataclass
class GetErdRelationshipsOutput(SuccessResult):
    matched_tables: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    table_columns: dict[str, list[str]] = field(default_factory=dict)


class GetErdRelationships(
    ToolBase[GetErdRelationshipsArgs, GetErdRelationshipsOutput]
):
    """Look up relationships and columns in the session's ERD data model.

    The session's mounted data source has a user-supplied ERD describing
    how tables relate. Given table names or fragments, returns matching
    relationships as `local_table.column -> referenced_table.column` plus
    the matched tables' column lists.

    Returns:
        Matching tables, their relationships, and their columns.
    """

    guidelines = ToolGuidelines(
        when_to_use=[
            "Before writing SQL that joins tables of the mounted data "
            "source, to find the correct join columns",
            "When you need a table's exact column names",
        ],
        avoid_if=[
            "No data source or ERD is mounted in this session",
        ],
    )

    def handle(
        self, args: GetErdRelationshipsArgs
    ) -> GetErdRelationshipsOutput:
        from marimo._fork.erd import load_erd

        index = load_erd()
        if index is None or not index.is_graph:
            return GetErdRelationshipsOutput(
                status="error",
                message=(
                    "No indexed ERD is available in this session. Fall "
                    "back to querying information_schema."
                ),
            )

        matched_tables, relationships = index.search(args.keywords)
        columns = {
            table: index.tables.get(table, []) for table in matched_tables[:10]
        }
        if not matched_tables and not relationships:
            return GetErdRelationshipsOutput(
                message=(
                    "No tables matched those keywords. Try shorter "
                    "fragments (e.g. 'invoice' instead of 'invoices')."
                ),
            )
        return GetErdRelationshipsOutput(
            matched_tables=matched_tables[:50],
            relationships=[r.render() for r in relationships],
            table_columns=columns,
            next_steps=[
                "Use these join columns to write the SQL query",
            ],
        )
