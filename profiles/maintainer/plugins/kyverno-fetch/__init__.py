"""kyverno-fetch plugin — one-call GraphQL PR-candidate fetch for pr-queue, plus an on-demand
diff-overlap check."""

from __future__ import annotations

from .tools import (
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


def register(ctx) -> None:
    ctx.register_tool(
        name="fetch_pr_candidates",
        toolset="kyverno-fetch",
        schema=FETCH_PR_CANDIDATES_SCHEMA,
        handler=handle_fetch_pr_candidates,
        description="One-call GraphQL fetch of every candidate PR's full metadata.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_pr_details",
        toolset="kyverno-fetch",
        schema=FETCH_PR_DETAILS_SCHEMA,
        handler=handle_fetch_pr_details,
        description="Full detail for an explicit list of PR numbers, after a digest-ranked pass.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_issue_candidates",
        toolset="kyverno-fetch",
        schema=FETCH_ISSUE_CANDIDATES_SCHEMA,
        handler=handle_fetch_issue_candidates,
        description="One-call GraphQL fetch of every candidate issue's full metadata.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_issue_details",
        toolset="kyverno-fetch",
        schema=FETCH_ISSUE_DETAILS_SCHEMA,
        handler=handle_fetch_issue_details,
        description="Full detail for an explicit list of issue numbers, after a digest-ranked pass.",
        emoji="📡",
    )
    ctx.register_tool(
        name="fetch_file_diff_overlap",
        toolset="kyverno-fetch",
        schema=FETCH_FILE_DIFF_OVERLAP_SCHEMA,
        handler=handle_fetch_file_diff_overlap,
        description="Checks whether two PRs touching the same file touch the same lines.",
        emoji="🔬",
    )
