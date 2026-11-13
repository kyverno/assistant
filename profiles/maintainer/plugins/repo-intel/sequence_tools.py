"""Tool schema + handler for `sequence_prs`. Thin: parse args, call sequencer.sequence, return
JSON. No GitHub/Slack access here — pr-queue fetches everything (via fetch_pr_candidates) and
passes it in."""

from __future__ import annotations

import json
from typing import Any, Dict

from . import pack, sequencer
from .fetch_tools import _repo


def _str(description: str) -> Dict[str, Any]:
    return {"type": "string", "description": description}


def _schema(name: str, description: str, properties: Dict[str, Any], required=None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    params["additionalProperties"] = False
    return {"name": name, "description": description, "parameters": params}


_PR_ITEM_SCHEMA = {
    "type": "object",
    "description": "One candidate PR's already-fetched metadata (straight from "
    "fetch_pr_candidates' output — no extra resolution needed).",
    "properties": {
        "number": {"type": "integer", "description": "PR number."},
        "changed_files": {"type": "array", "items": {"type": "string"},
                           "description": "Every changed file path."},
        "labels": {"type": "array", "items": {"type": "string"},
                   "description": "This PR's own labels — checked for the gate's bypass label."},
        "base_branch": _str("PR's base branch name."),
        "head_branch": _str("PR's head branch name."),
        "body": _str("Raw PR body text — scanned for an explicit 'Depends on #N' / "
                      "'Blocked by #N' / 'Requires #N' / 'Stacked on #N' reference to "
                      "another candidate. No need to pre-parse this yourself."),
        "closing_issues": {"type": "array", "items": {"type": ["integer", "object"]},
                            "description": "Issue numbers (or issue dicts with a 'number' key) "
                            "this PR closes. Two candidates sharing one go to unresolved[] — "
                            "only one can actually close it."},
        "files_truncated": {"type": "boolean",
                             "description": "fetch_pr_candidates' files_truncated: changed_files is "
                             "cut off at the fetch limit. Flags the PR in its warnings, since its "
                             "classification and overlaps may be incomplete."},
        "dependency_bumps": {"type": "array", "items": {"type": "string"},
                              "description": "Module paths this PR bumps (Dependabot PRs). "
                              "A bump of a module the organisation pack lists as a generated-file "
                              "input counts as an input touch even though no input path appears "
                              "in changed_files."},
    },
    "required": ["number"],
    "additionalProperties": False,
}

SEQUENCE_PRS_SCHEMA = _schema(
    "sequence_prs",
    "Build the hard-dependency graph for a candidate set and layer it into tiers — a graph "
    "builder, not a decision maker. Four hard-edge types, all derived mechanically from the "
    "input (no hints needed from you): stacked branches, generated-file input-before-output, "
    "an explicit 'Depends on #N'-style reference in a PR's own body, and a closing-issue "
    "conflict (goes to unresolved[], not an edge — only one PR can close a given issue). "
    "Cycles are detected and excluded from tiers. Within a tier, there is no hard dependency "
    "between any two PRs — ordering them is your call, using Slack context, mnemosyne "
    "history, and whatever the maintainer said they care about this session; cite why. Also "
    "returns package_overlaps (same directory, no hard edge — a real review-risk signal) and "
    "gate_blocked per PR (an open gate-failure issue blocks merging on that branch regardless "
    "of files touched, unless the PR carries the gate's bypass label; only when the organisation "
    "pack defines a gate) — state gate_blocked plainly, and make any bypass suggestion yourself "
    "by reading the failure issue, never mechanically. File classes and generated-file inputs come "
    "from the pack too: without one, every file reads STANDARD.",
    {
        "prs": {"type": "array", "items": _PR_ITEM_SCHEMA,
                "description": "Every candidate PR (ready-for-review + needs-review)."},
        "repo_default_branch": _str("Repo's default branch (e.g. 'main') — a PR whose base "
                                     "equals this is never 'stacked'."),
        "blocking_issue_branches": {
            "type": "array",
            "items": {"type": ["string", "null"]},
            "description": "One entry per currently-open gate-failure issue: the branch its "
            "marker names, or null if it has no marker (which blocks every branch — see "
            "the repo-context skill's branch-gate section). Empty/omitted means no gate is open "
            "anywhere right now.",
        },
        "repo": _str("'owner/repo' the PRs come from. Omit to use the profile's repository. A body "
                      "reference naming another repository is not about a candidate."),
    },
    required=["prs"],
)


def handle_sequence_prs(args: Dict[str, Any], **_kw) -> str:
    prs = args.get("prs") or []
    if not isinstance(prs, list) or not prs:
        return json.dumps({"success": False, "error": "prs must be a non-empty array"})
    try:
        rules = pack.load()
    except pack.PackError as exc:
        return json.dumps({"success": False, "error": f"organisation pack: {exc}"})
    try:
        result = sequencer.sequence(
            pr_dicts=prs,
            repo_default_branch=str(args.get("repo_default_branch") or "main"),
            blocking_issue_branches=args.get("blocking_issue_branches") or [],
            rules=rules,
            repo=_repo(args),
        )
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"success": False, "error": f"bad input: {exc}"})
    return json.dumps({"success": True, "pack": rules.source or "none (no file classes or gate)", **result})
