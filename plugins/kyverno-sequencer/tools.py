"""Tool schema + handler for `sequence_prs`. Thin: parse args, call sequencer.sequence, return
JSON. No GitHub/Slack access here — pr-queue fetches everything (via fetch_pr_candidates) and
passes it in."""

from __future__ import annotations

import json
from typing import Any, Dict

from . import sequencer


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
                   "description": "This PR's own labels — checked for e2e-gate-bypass."},
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
                              "description": "Go module paths this PR bumps (Dependabot PRs). "
                              "A github.com/kyverno/api bump counts as a generated-file-input "
                              "touch even though no api/**/*_types.go path appears in "
                              "changed_files."},
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
    "gate_blocked per PR (an open e2e-failure issue blocks merging on that branch regardless "
    "of files touched, unless the PR carries e2e-gate-bypass) — state gate_blocked plainly, "
    "and make any bypass suggestion yourself by reading the failure issue, never mechanically.",
    {
        "prs": {"type": "array", "items": _PR_ITEM_SCHEMA,
                "description": "Every candidate PR (ready-for-review + needs-review)."},
        "repo_default_branch": _str("Repo's default branch (e.g. 'main') — a PR whose base "
                                     "equals this is never 'stacked'."),
        "blocking_issue_branches": {
            "type": "array",
            "items": {"type": ["string", "null"]},
            "description": "One entry per currently-open e2e-failure issue: the branch its "
            "marker names, or null if it has no marker (which blocks every branch — see "
            "kyverno-context's e2e-gate reference). Empty/omitted means no gate is open "
            "anywhere right now.",
        },
    },
    required=["prs"],
)


def handle_sequence_prs(args: Dict[str, Any], **_kw) -> str:
    prs = args.get("prs") or []
    if not isinstance(prs, list) or not prs:
        return json.dumps({"success": False, "error": "prs must be a non-empty array"})
    try:
        result = sequencer.sequence(
            pr_dicts=prs,
            repo_default_branch=str(args.get("repo_default_branch") or "main"),
            blocking_issue_branches=args.get("blocking_issue_branches") or [],
        )
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"success": False, "error": f"bad input: {exc}"})
    return json.dumps({"success": True, **result})
