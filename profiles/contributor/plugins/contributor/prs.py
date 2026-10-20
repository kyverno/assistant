"""PR facts: related_prs (precedents in the same area) and my_pr_state (where the contributor's PR stands)."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import rules
from .github import excerpt


PR_FIELDS = """
number title url state merged mergedAt closedAt updatedAt
author { login }
additions deletions changedFiles
labels(first: 20) { nodes { name } }
files(first: 40) { nodes { path } }
reviews(last: 15) { nodes { author { login } authorAssociation state } }
closingIssuesReferences(first: 5) { nodes { number } }
reviewThreads { totalCount }
comments { totalCount }
"""

PR_SEARCH = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 20, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { %s } }
  }
}
""" % PR_FIELDS


def label_names(node: Dict[str, Any]) -> List[str]:
    return [n["name"] for n in node["labels"]["nodes"]]


def _pr_row(pr: Dict[str, Any], overlap: List[str], match: str) -> Dict[str, Any]:
    reviewers: Dict[str, str] = {}
    for r in pr["reviews"]["nodes"]:
        login = (r.get("author") or {}).get("login")
        if login:
            reviewers[login] = r["authorAssociation"]
    return {
        "number": pr["number"], "url": pr["url"], "title": pr["title"],
        "state": "merged" if pr["merged"] else pr["state"].lower(),
        "merged_at": pr.get("mergedAt"), "author": (pr.get("author") or {}).get("login"),
        "size": {"additions": pr["additions"], "deletions": pr["deletions"], "changed_files": pr["changedFiles"]},
        "labels": label_names(pr), "overlap_files": overlap[:10], "overlap_count": len(overlap), "match": match,
        "reviewers": [{"login": k, "association": v} for k, v in reviewers.items()],
        "linked_issues": [n["number"] for n in pr["closingIssuesReferences"]["nodes"]],
        "review_threads": pr["reviewThreads"]["totalCount"], "comments": pr["comments"]["totalCount"],
    }


def _search_prs(client, query: str, pages: int) -> List[Dict[str, Any]]:
    out, after = [], None
    for _ in range(pages):
        data = client.graphql(PR_SEARCH, {"q": query, "after": after})["search"]
        out += [n for n in data["nodes"] if n]
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    return out


def related_prs(client, org: Dict[str, Any], repo: str, paths: Optional[List[str]] = None,
                areas: Optional[List[str]] = None, state: str = "merged", limit: int = 15,
                scan_pages: int = 4, ref: Optional[str] = None) -> Dict[str, Any]:
    """PRs that changed the same area. Labeled PRs come from a label search; older unlabeled PRs
    are found by scanning recent PRs and applying the repository's own path rules to their files."""
    paths, areas = list(paths or []), list(areas or [])
    if not paths and not areas:
        return {"success": False, "error": "give paths or areas"}
    sha = ref
    if not sha:
        _, sha = client.default_branch_sha(repo)
    label_rules = rules.label_rules_at(client, org, repo, sha)
    if paths:
        areas += [a for a in rules.area_labels(label_rules, org, paths) if a not in areas]
    state_q = {"merged": "is:merged", "open": "is:open", "closed": "is:closed is:unmerged"}[state]
    found: Dict[int, Dict[str, Any]] = {}

    
    for area in areas:
        for pr in _search_prs(client, f'repo:{repo} is:pr {state_q} label:"{area}" sort:updated-desc', 1):
            files = [n["path"] for n in pr["files"]["nodes"]]
            overlap = [f for f in files if f in paths or any(f.startswith(p.rstrip("/") + "/") for p in paths)]
            found[pr["number"]] = _pr_row(pr, overlap, "label")
    for pr in _search_prs(client, f"repo:{repo} is:pr {state_q} sort:updated-desc", scan_pages):
        if pr["number"] in found:
            continue
        files = [n["path"] for n in pr["files"]["nodes"]]
        hit = rules.area_labels(label_rules, org, files)
        if any(a in hit for a in areas):
            overlap = sorted({p for a in areas for p in hit.get(a, [])})
            found[pr["number"]] = _pr_row(pr, overlap, "path-rules")
    rows = sorted(found.values(), key=lambda r: (-r["overlap_count"], r["merged_at"] or ""), reverse=False)
    rows.sort(key=lambda r: (r["overlap_count"], r["merged_at"] or ""), reverse=True)
    return {"success": True, "repo": repo, "areas": areas, "state": state, "count": len(rows),
            "prs": rows[:limit],
            "note": f"label matches plus the newest {scan_pages * 20} PRs checked against {org['labels']['source']}; "
                    "older unlabeled PRs beyond that window are not covered"}


# -- my_pr_state ---------------------------------------------------------------------------




MAINTAINER = ("OWNER", "MEMBER", "COLLABORATOR")
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED", "STALE", "STARTUP_FAILURE"}

QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name target { ... on Commit { statusCheckRollup { contexts(first: 100) { nodes {
      __typename ... on CheckRun { name conclusion } ... on StatusContext { context state } } } } } } }
    pullRequest(number: $number) {
      number title url state merged isDraft mergeable mergeStateStatus updatedAt createdAt
      baseRefName headRefName author { login }
      labels(first: 30) { nodes { name } }
      reviewDecision
      reviews(last: 50) { nodes { author { login } authorAssociation state submittedAt url body } }
      reviewThreads(first: 100) { nodes { isResolved isOutdated path line comments(first: 8) {
        nodes { author { login } authorAssociation body createdAt url } } } }
      comments(last: 30) { nodes { author { login } authorAssociation body createdAt url } }
      commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
        __typename ... on CheckRun { name conclusion detailsUrl } ... on StatusContext { context state targetUrl } } } } } } }
    }
  }
}
"""


def _login(node: Optional[Dict[str, Any]]) -> str:
    return ((node or {}).get("author") or {}).get("login") or "ghost"


def _who(login: str, assoc: str, author: str, bots: List[str]) -> str:
    if login == author:
        return "author"
    if any(login.lower().startswith(b.lower()) for b in bots) or login.endswith("[bot]"):
        return "bot"
    return "maintainer" if assoc in MAINTAINER else "other_human"


def _checks(contexts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in contexts:
        if not c:
            continue
        name = c.get("name") or c.get("context")
        result = c.get("conclusion") or c.get("state")
        out.append({"name": name, "result": result, "url": c.get("detailsUrl") or c.get("targetUrl")})
    return out


def my_pr_state(client, org: Dict[str, Any], repo: str, number: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    owner, name = repo.split("/", 1)
    repo_data = client.graphql(QUERY, {"owner": owner, "name": name, "number": number})["repository"]
    pr = repo_data["pullRequest"]
    if pr is None:
        return {"success": False, "error": f"PR #{number} not found in {repo}"}
    author, bots = _login(pr), org["review_bots"]

    threads = []
    for t in pr["reviewThreads"]["nodes"]:
        comments = t["comments"]["nodes"]
        if not comments:
            continue
        first = comments[0]
        threads.append({
            "resolved": t["isResolved"], "outdated": t["isOutdated"], "path": t["path"], "line": t["line"],
            "opened_by": _login(first), "opened_by_kind": _who(_login(first), first["authorAssociation"], author, bots),
            "replies": len(comments) - 1, "last_reply_by": _login(comments[-1]),
            "last_reply_by_author": _login(comments[-1]) == author,
            "url": first["url"], **(excerpt(first["body"], first["url"], 500) or {}),
        })
    groups: Dict[str, List[Dict[str, Any]]] = {"maintainer": [], "other_human": [], "bot": []}
    for t in threads:
        if not t["resolved"] and t["opened_by_kind"] in groups:
            groups[t["opened_by_kind"]].append(t)

    reviews = [{"by": _login(r), "kind": _who(_login(r), r["authorAssociation"], author, bots), "state": r["state"],
                "at": r["submittedAt"], "url": r["url"]} for r in pr["reviews"]["nodes"] if _login(r) != author]
    conversation = [{"by": _login(c), "kind": _who(_login(c), c["authorAssociation"], author, bots), "at": c["createdAt"],
                     "url": c["url"], **(excerpt(c["body"], c["url"], 400) or {})} for c in pr["comments"]["nodes"]]

    commit = pr["commits"]["nodes"][0]["commit"] if pr["commits"]["nodes"] else {}
    rollup = (commit.get("statusCheckRollup") or {}).get("contexts", {}).get("nodes", [])
    checks = _checks(rollup)
    base_roll = (((repo_data.get("defaultBranchRef") or {}).get("target") or {}).get("statusCheckRollup") or {}) \
        .get("contexts", {}).get("nodes", [])
    base_failed = {c["name"] for c in _checks(base_roll) if c["result"] in FAILED}
    infra = set(org["readiness"].get("infra_checks", []))
    failing = [{**c, "also_failing_on_base_branch": c["name"] in base_failed}
               for c in checks if c["result"] in FAILED and c["name"] not in infra]
    infra_failing = [c["name"] for c in checks if c["result"] in FAILED and c["name"] in infra]
    pending = [c["name"] for c in checks if c["result"] in (None, "PENDING", "EXPECTED")]

    labels = [n["name"] for n in pr["labels"]["nodes"]]
    last = max([pr["updatedAt"]] + [c["at"] for c in conversation] + [r["at"] for r in reviews if r["at"]])
    days_idle = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
    is_open = pr["state"] == "OPEN"
    # A merged or closed PR has nothing left for its author to do, and its labels are history.
    attention = [l for l in labels if l in org["readiness"]["attention_labels"]] if is_open else []

    # Only each reviewer's latest verdict counts: a later approval (or a dismissal) replaces an earlier request.
    verdict: Dict[str, str] = {}
    for r in sorted(reviews, key=lambda r: r["at"] or ""):
        if r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            verdict[r["by"]] = r["state"]
    asking = sorted(by for by, state in verdict.items() if state == "CHANGES_REQUESTED")

    needs = []
    if is_open:
        if pr["mergeable"] == "CONFLICTING":
            needs.append("merge conflicts with the base branch")
        if failing:
            needs.append("failing checks: " + ", ".join(f["name"] for f in failing))
        if asking:
            needs.append("changes requested by " + ", ".join(asking))
        open_threads = sum(len(v) for v in groups.values())
        if open_threads:
            needs.append(f"{open_threads} unresolved review thread(s)")

    return {"success": True, "repo": repo,
            "pr": {"number": pr["number"], "url": pr["url"], "title": pr["title"], "author": author,
                   "state": "merged" if pr["merged"] else pr["state"].lower(), "is_draft": pr["isDraft"],
                   "labels": labels, "review_decision": pr["reviewDecision"], "merge_state": pr["mergeStateStatus"],
                   "merge_conflicts": pr["mergeable"] == "CONFLICTING", "days_since_activity": days_idle},
            "attention_labels": attention,
            "ready_labels": [l for l in labels if l in org["readiness"]["ready_labels"]],
            "needs_from_author": needs,
            "unresolved_threads": groups, "resolved_threads": sum(1 for t in threads if t["resolved"]),
            "reviews": reviews, "conversation": conversation,
            "failing_checks": failing, "pending_checks": pending,
            "infra_checks_failing": infra_failing,
            "readiness_checks_file": org["readiness"]["checks_file"],
            "note": "bot findings are sometimes wrong (see REVIEW.md); the contributor decides what to accept "
                    "and writes every reply"}
