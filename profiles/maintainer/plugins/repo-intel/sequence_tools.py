"""Tool schema + handler for `sequence_prs`. Thin: take PR numbers, read their records from the last fetch (fetching
any that are missing), read the gate and default branch from the repository, call sequencer.sequence, return JSON."""

from __future__ import annotations

import json
from typing import Any, Dict

from . import fetch, pack, sequencer
from .fetch_tools import _repo


def _schema(name: str, description: str, properties: Dict[str, Any], required=None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    params["additionalProperties"] = False
    return {"name": name, "description": description, "parameters": params}


SEQUENCE_PRS_SCHEMA = _schema(
    "sequence_prs",
    "Build the hard-dependency graph for a set of PRs and layer it into tiers: a graph builder, not a decision "
    "maker. Pass only PR numbers; the records come from the last fetch_pr_candidates/fetch_pr_details call "
    "(any missing are fetched). Four hard-edge types are derived mechanically: stacked branches, generated-file "
    "input before output, an explicit 'Depends on #N'-style body reference, and a shared closing issue (goes to "
    "unresolved[], since only one PR can close it). Cycles are excluded from tiers. Within a tier there is no hard "
    "dependency; order them yourself and cite why. Also returns file_overlaps, package_overlaps (same directory, "
    "a review-together signal) and, when the organisation pack has a gate, gate_status (read from the repository's "
    "open failure issues by this tool) and gate_blocked per PR. State gate_blocked plainly; a bypass suggestion is "
    "your own read of the failure issue, never mechanical.",
    {
        "pr_numbers": {"type": "array", "items": {"type": "integer"}, "description": "The candidate PRs."},
        "repo": {"type": "string", "description": "'owner/repo'. Omit to use the profile's repository."},
    },
    required=["pr_numbers"],
)


def handle_sequence_prs(args: Dict[str, Any], **_kw) -> str:
    numbers = args.get("pr_numbers") or []
    repo = _repo(args)
    if not isinstance(numbers, list) or not numbers:
        return json.dumps({"success": False, "error": "pr_numbers must be a non-empty array"})
    if not repo or "/" not in str(repo):
        return json.dumps({"success": False, "error": "repo (or ASSISTANT_REPO) must be 'owner/repo'"})
    repo = str(repo)
    try:
        rules = pack.load(repo=repo)
    except pack.PackError as exc:
        return json.dumps({"success": False, "error": f"organisation pack: {exc}"})
    try:
        numbers = [int(n) for n in numbers]
        prs, missing = fetch.cached_prs(repo, numbers)
        errors: list[str] = []
        if missing:
            got = fetch.fetch_pr_details(repo, missing)
            errors = got["errors"]
            prs += fetch.cached_prs(repo, missing)[0]
        owner, name = repo.split("/", 1)
        state = fetch.repo_state(owner, name, rules)
    except fetch.FetchError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    except (TypeError, ValueError) as exc:
        return json.dumps({"success": False, "error": f"bad input: {exc}"})
    gate = state.get("gate") or {}
    blocking: list = list(gate.get("blocking_branches") or [])
    if gate.get("blocks_all_branches"):
        blocking.append(None)
    result = sequencer.sequence(
        pr_dicts=prs,
        repo_default_branch=state["default_branch"],
        blocking_issue_branches=blocking,
        rules=rules,
        repo=repo,
    )
    if gate:
        result["gate_status"]["open_failure_issues"] = gate["open_failure_issues"]
    return json.dumps({"success": True, "pack": rules.source or "none (no file classes or gate)",
                       **({"errors": errors} if errors else {}), **result})
