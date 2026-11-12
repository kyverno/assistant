"""Tool schema + handler for `fetch_pr_candidates`. One call replaces pr-queue's old
search_pull_requests + one pull_request_read per PR sequence."""

from __future__ import annotations

import json
from typing import Any, Dict

from .fetch import (
    FetchError,
    fetch_file_diff_overlap,
    fetch_issue_candidates,
    fetch_issue_details,
    fetch_pr_candidates,
    fetch_pr_details,
)


def _repo(args: Dict[str, Any]):
    """`repo` arg, else this profile's KYVERNO_REPO (scoped under a multiplexing gateway)."""
    if args.get("repo"):
        return args["repo"]
    try:
        from agent.secret_scope import get_secret  # type: ignore[import-not-found]
    except ImportError:
        import os

        return os.environ.get("KYVERNO_REPO")
    return get_secret("KYVERNO_REPO")


def _schema(name: str, description: str, properties: Dict[str, Any], required=None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        params["required"] = required
    return {"name": name, "description": description, "parameters": params}


FETCH_PR_CANDIDATES_SCHEMA = _schema(
    "fetch_pr_candidates",
    "Fetch every candidate PR's full metadata in one call via GitHub's GraphQL API directly "
    "(the profile's own REPO_GITHUB_TOKEN, same scopes already granted to the github MCP server) — "
    "replaces a search_pull_requests + one pull_request_read per PR sequence with a single "
    "tool invocation. Returns, per PR: title, url, body, author, author_association "
    "(OWNER/MEMBER/COLLABORATOR/CONTRIBUTOR/FIRST_TIME_CONTRIBUTOR/NONE — cite this, a "
    "first-time contributor's PR generally warrants a closer look), created_at, base/head "
    "branch, size (files/additions/deletions — a review-effort signal, distinct from "
    "priority), milestone, milestone_due_on (cite when close — real urgency, not a guess), "
    "labels, changed_files, unresolved_review_threads, "
    "coderabbit_approved, ci_state (the PR's own checks only, excluding the E2E Gate "
    "status — see gate_blocked for that), closing_issues (each with its own milestone, "
    "whether it's open, and its own labels — release-* lives here, not on the PR), and "
    "external_references: for a body reference ('Parent: #N', 'Depends on #N', ...) to a "
    "number outside this fetched set, its real kind/state/title. Cite that directly; never "
    "tell the maintainer to go check it themselves. Also per PR: is_dependabot, merge_state "
    "(GitHub's mergeStateStatus: CLEAN/BLOCKED/UNSTABLE/DIRTY/BEHIND), and copilot_review "
    "(Copilot's latest verdict heading, whether it is the approving '🟢 Approved', its one-line "
    "summary and finding count; null if Copilot hasn't reviewed). For a Dependabot PR: bumps "
    "(each dependency's name/from/to/update_type/group, read from the commit trailers) and "
    "semver_level (major/minor/patch, or unknown if any entry is unrecognized — the same rule "
    "the triage workflow uses, so it is available before that workflow has labelled the PR). "
    "Pass changed_files/labels/base_branch/"
    "head_branch/body/dependency_bumps and each closing issue's number into sequence_prs; "
    "everything else stays with you for citing when ordering within a tier. "
    "include_stale_detail=true adds, per PR: maintainer_can_modify, staled_at (when the "
    "`stale` label actually landed, from the timeline — null if never staled), "
    "last_commit_at, comment_count, author_comments_since_stale (the PR author's own "
    "comments after staled_at), and per closing issue its real assignees and "
    "other_closing_prs (every other open PR also claiming to close it). Costs a second "
    "query per PR — only set it true for a search_query already scoped to a handful of "
    "PRs (e.g. 'label:stale'), never for a normal queue build. "
    "detail='digest' (default 'full') drops body/copilot_review text and caps limit at 60 — "
    "use it to rank or count across a whole candidate set without spilling over, then call "
    "fetch_pr_details for just the chosen numbers to get the dropped fields back. Only use "
    "digest when ranking by something search can't filter on directly (closing-issue "
    "severity, Dependabot semver/security level, 'superseded by a merged PR') — a search "
    "qualifier (milestone:, sort:, a label) already narrows the result on its own, no digest "
    "needed.",
    {
        "repo": {"type": "string", "description": "'owner/repo'. Omit to use KYVERNO_REPO."},
        "search_query": {
            "type": "string",
            "description": "Extra GitHub search qualifiers beyond 'repo:/is:pr/is:open/"
            "draft:false', which this tool adds itself — e.g. 'label:ready-for-review', "
            "'label:ready-for-review milestone:\"Kyverno Release 1.20.0\"', "
            "'label:needs-review', 'label:workflow-approval-required', 'author:someuser', "
            "'author:app/dependabot' (every open Dependabot PR, labelled or not), "
            "'label:stale milestone:\"Kyverno Release 1.20.0\"' (stale PRs scoped to one "
            "milestone, with include_stale_detail=true).",
        },
        "limit": {
            "type": "integer",
            "description": "Max PRs to fetch full detail for (default 15, capped at 60 when "
            "detail='digest'). total_count in the result is the real total match count, "
            "accurate even when truncated.",
        },
        "include_stale_detail": {
            "type": "boolean",
            "description": "Default false. Set true only when the search is already "
            "narrowed to author-blocked PRs (e.g. 'label:stale' or 'label:needs-author-action') "
            "and you need the extra fields described above.",
        },
        "detail": {
            "type": "string",
            "enum": ["full", "digest"],
            "description": "Default 'full'. 'digest' drops long free text so a whole "
            "candidate set can be ranked without risking a spilled-over result.",
        },
    },
    required=["search_query"],
)


def handle_fetch_pr_candidates(args: Dict[str, Any], **_kw) -> str:
    repo = _repo(args)
    search_query = args.get("search_query")
    if not repo or not search_query:
        return json.dumps({"success": False, "error": "repo (or KYVERNO_REPO) and search_query are required"})
    try:
        result = fetch_pr_candidates(
            repo=str(repo),
            search_query=str(search_query),
            limit=int(args.get("limit") or 15),
            include_stale_detail=bool(args.get("include_stale_detail") or False),
            detail=str(args.get("detail") or "full"),
        )
    except FetchError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    return json.dumps({"success": True, **result})


FETCH_PR_DETAILS_SCHEMA = _schema(
    "fetch_pr_details",
    "Phase 2 of a digest-then-rank pass: full detail (body, diffs' file list, review text) "
    "for an explicit list of PR numbers already chosen from a fetch_pr_candidates(detail="
    "'digest') result — skips the search step. Same per-PR fields as fetch_pr_candidates' "
    "full mode.",
    {
        "repo": {"type": "string", "description": "'owner/repo'. Omit to use KYVERNO_REPO."},
        "pr_numbers": {"type": "array", "items": {"type": "integer"},
                        "description": "The exact PR numbers to fetch full detail for."},
        "include_stale_detail": {
            "type": "boolean",
            "description": "Default false. Same meaning as on fetch_pr_candidates.",
        },
    },
    required=["pr_numbers"],
)


def handle_fetch_pr_details(args: Dict[str, Any], **_kw) -> str:
    repo = _repo(args)
    pr_numbers = args.get("pr_numbers") or []
    if not repo or not pr_numbers:
        return json.dumps({"success": False, "error": "repo (or KYVERNO_REPO) and pr_numbers are required"})
    try:
        result = fetch_pr_details(
            repo=str(repo),
            pr_numbers=[int(n) for n in pr_numbers],
            include_stale_detail=bool(args.get("include_stale_detail") or False),
        )
    except FetchError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    return json.dumps({"success": True, **result})


FETCH_ISSUE_CANDIDATES_SCHEMA = _schema(
    "fetch_issue_candidates",
    "Fetch every candidate issue's full metadata in one call via GitHub's GraphQL API "
    "directly (same REPO_GITHUB_TOKEN as fetch_pr_candidates) — replaces a search_issues + one "
    "issue_read per issue sequence. Returns, per issue: title, url, body, author, "
    "author_association, created_at, state, state_reason, milestone/milestone_open/"
    "milestone_due_on, labels, assignees, comment_count and recent_comments (last 20, for "
    "spotting a reply after needs-more-info or a 'fixed in vX' claim), closing_prs (every "
    "open PR that references closing this issue, resolved by GitHub's own keyword parser — "
    "not just the one that eventually merges; more than one is a real, normal outcome, not "
    "a bug), closed_by (the PR number or commit that actually closed it, from the timeline, "
    "null if still open or outside the fetched window), and reopened_since_closed (whether a "
    "reopen event exists in that same window). closed_by plus a merged PR titled or bodied "
    "'Revert ...' naming that PR number is the live signal for a reverted-closer issue — "
    "this tool returns the facts, not that verdict. Also parent/sub_issues/"
    "tracked_in_issues — GitHub's own sub-issue/tracking links, real when present but rare "
    "in practice (most contributors state a relationship as prose instead, in the body you "
    "already have); absence here is not evidence of no relationship. "
    "detail='digest' (default 'full') drops body/recent_comments and caps limit at 60 — use "
    "it to rank or count across a whole candidate set without spilling over (e.g. 'issues "
    "with no closing_prs entry'), then call fetch_issue_details for just the chosen numbers. "
    "Only use digest for a filter search can't express directly — a label/milestone/sort "
    "qualifier already narrows the result on its own.",
    {
        "repo": {"type": "string", "description": "'owner/repo'. Omit to use KYVERNO_REPO."},
        "search_query": {
            "type": "string",
            "description": "Extra GitHub search qualifiers beyond 'repo:/is:issue', which "
            "this tool adds itself — e.g. 'is:open', 'is:open -label:triage', "
            "'is:open created:>2026-09-28' (new since last session), 'is:open "
            "label:needs-more-info', 'is:closed closed:>2026-09-01' (for staleness/revert "
            "checks on recently-closed issues).",
        },
        "limit": {
            "type": "integer",
            "description": "Max issues to fetch full detail for (default 15, capped at 60 "
            "when detail='digest'). total_count in the result is the real total match count, "
            "accurate even when truncated.",
        },
        "detail": {
            "type": "string",
            "enum": ["full", "digest"],
            "description": "Default 'full'. 'digest' drops body/recent_comments so a whole "
            "candidate set can be ranked without risking a spilled-over result.",
        },
    },
    required=["search_query"],
)


def handle_fetch_issue_candidates(args: Dict[str, Any], **_kw) -> str:
    repo = _repo(args)
    search_query = args.get("search_query")
    if not repo or not search_query:
        return json.dumps({"success": False, "error": "repo (or KYVERNO_REPO) and search_query are required"})
    try:
        result = fetch_issue_candidates(
            repo=str(repo),
            search_query=str(search_query),
            limit=int(args.get("limit") or 15),
            detail=str(args.get("detail") or "full"),
        )
    except FetchError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    return json.dumps({"success": True, **result})


FETCH_ISSUE_DETAILS_SCHEMA = _schema(
    "fetch_issue_details",
    "Phase 2 of a digest-then-rank pass: full detail (body, recent_comments) for an explicit "
    "list of issue numbers already chosen from a fetch_issue_candidates(detail='digest') "
    "result — skips the search step. Same per-issue fields as fetch_issue_candidates' full "
    "mode.",
    {
        "repo": {"type": "string", "description": "'owner/repo'. Omit to use KYVERNO_REPO."},
        "issue_numbers": {"type": "array", "items": {"type": "integer"},
                           "description": "The exact issue numbers to fetch full detail for."},
    },
    required=["issue_numbers"],
)


def handle_fetch_issue_details(args: Dict[str, Any], **_kw) -> str:
    repo = _repo(args)
    issue_numbers = args.get("issue_numbers") or []
    if not repo or not issue_numbers:
        return json.dumps({"success": False, "error": "repo (or KYVERNO_REPO) and issue_numbers are required"})
    try:
        result = fetch_issue_details(repo=str(repo), issue_numbers=[int(n) for n in issue_numbers])
    except FetchError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    return json.dumps({"success": True, **result})


FETCH_FILE_DIFF_OVERLAP_SCHEMA = _schema(
    "fetch_file_diff_overlap",
    "On-demand follow-up for a file_overlaps entry from sequence_prs: fetches each listed "
    "PR's real diff hunks for one specific file. Returns raw patches only, no computed "
    "verdict — each PR's hunk line numbers are relative to its own merge-base with main, "
    "which can differ between PRs, so comparing line numbers across PRs isn't reliable. "
    "Read the patches yourself and describe what each change actually does and whether they "
    "genuinely interact. Only call this for a file_overlaps pair worth the extra look, not "
    "for every one automatically.",
    {
        "repo": {"type": "string", "description": "'owner/repo'. Omit to use KYVERNO_REPO."},
        "path": {"type": "string", "description": "The exact file path both PRs changed."},
        "pr_numbers": {"type": "array", "items": {"type": "integer"},
                        "description": "The PRs to compare (usually 2, from one file_overlaps entry)."},
    },
    required=["path", "pr_numbers"],
)


def handle_fetch_file_diff_overlap(args: Dict[str, Any], **_kw) -> str:
    repo = _repo(args)
    path = args.get("path")
    pr_numbers = args.get("pr_numbers") or []
    if not repo or not path or not pr_numbers:
        return json.dumps({"success": False, "error": "repo, path, and pr_numbers are required"})
    try:
        result = fetch_file_diff_overlap(repo=str(repo), path=str(path), pr_numbers=[int(n) for n in pr_numbers])
    except FetchError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    return json.dumps({"success": True, **result})
