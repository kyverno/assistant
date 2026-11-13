"""repo-intel plugin — the maintainer assistant's repository data: PR and issue fetch, plus the
deterministic merge-sequencing graph built over what was fetched."""

from __future__ import annotations

from .fetch_tools import (
    FETCH_FILE_DIFF_OVERLAP_SCHEMA,
    FETCH_ISSUE_CANDIDATES_SCHEMA,
    FETCH_ISSUE_DETAILS_SCHEMA,
    FETCH_PR_CANDIDATES_SCHEMA,
    FETCH_PR_DETAILS_SCHEMA,
    handle_fetch_file_diff_overlap,
    handle_fetch_issue_candidates,
    handle_fetch_issue_details,
    handle_fetch_pr_candidates,
    handle_fetch_pr_details,
)
from .pack_tools import REPO_PACK_SCHEMA, handle_repo_pack
from .sequence_tools import SEQUENCE_PRS_SCHEMA, handle_sequence_prs


def register(ctx) -> None:
    ctx.register_tool(
        name="fetch_pr_candidates",
        toolset="repo-intel",
        schema=FETCH_PR_CANDIDATES_SCHEMA,
        handler=handle_fetch_pr_candidates,
        description="One-call GraphQL fetch of every candidate PR's full metadata.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_pr_details",
        toolset="repo-intel",
        schema=FETCH_PR_DETAILS_SCHEMA,
        handler=handle_fetch_pr_details,
        description="Full detail for an explicit list of PR numbers, after a digest-ranked pass.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_issue_candidates",
        toolset="repo-intel",
        schema=FETCH_ISSUE_CANDIDATES_SCHEMA,
        handler=handle_fetch_issue_candidates,
        description="One-call GraphQL fetch of every candidate issue's full metadata.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_issue_details",
        toolset="repo-intel",
        schema=FETCH_ISSUE_DETAILS_SCHEMA,
        handler=handle_fetch_issue_details,
        description="Full detail for an explicit list of issue numbers, after a digest-ranked pass.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_file_diff_overlap",
        toolset="repo-intel",
        schema=FETCH_FILE_DIFF_OVERLAP_SCHEMA,
        handler=handle_fetch_file_diff_overlap,
        description="Checks whether two PRs touching the same file touch the same lines.",
        emoji="🔬",
    )
    ctx.register_tool(
        name="sequence_prs",
        toolset="repo-intel",
        schema=SEQUENCE_PRS_SCHEMA,
        handler=handle_sequence_prs,
        description="Deterministic candidate merge-sequence: hard-dependency graph, cycle detection, "
        "tiers, file and package overlaps, gate status.",
        emoji="🧮",
    )
    ctx.register_tool(
        name="repo_pack",
        toolset="repo-intel",
        schema=REPO_PACK_SCHEMA,
        handler=handle_repo_pack,
        description="The organisation pack: gates, file classes, labels and the organisation's own facts.",
        emoji="📦",
    )
