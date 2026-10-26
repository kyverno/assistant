"""PR facts: related_prs (earlier PRs on the same paths, with what the maintainers said) and my_pr_state (where the
contributor's PR stands)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import rules
from .github import excerpt


MAINTAINER = ("OWNER", "MEMBER", "COLLABORATOR")
_PR_REF = re.compile(r"\(#(\d+)\)")

CANDIDATES = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 30) {
    nodes { ... on PullRequest { number files(first: 50) { nodes { path } } } }
  }
}
"""

DISCUSSION = """
fragment F on PullRequest {
  number title url state merged mergedAt closedAt additions deletions changedFiles
  author { login __typename }
  labels(first: 15) { nodes { name } }
  closingIssuesReferences(first: 3) { nodes { number title url } }
  reviews(first: 20) { nodes { author { login __typename } authorAssociation state body url } }
  reviewThreads(first: 20) { totalCount nodes { comments(first: 1) { nodes { author { login __typename } authorAssociation body url } } } }
  comments(last: 12) { nodes { author { login __typename } authorAssociation body url } }
  timelineItems(last: 3, itemTypes: [CLOSED_EVENT]) { nodes { ... on ClosedEvent { closer { ... on PullRequest { number url } } } } }
}
query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { %s } }
"""


def label_names(node: Dict[str, Any]) -> List[str]:
    return [n["name"] for n in node["labels"]["nodes"]]


def _merged_candidates(client, repo: str, sha: str, paths: List[str]) -> Dict[int, Dict[str, Any]]:
    """Merged PRs that changed these paths, from the commit history of each path (squash commits end in `(#123)`)."""
    found: Dict[int, Dict[str, Any]] = {}
    histories = client.parallel(*[(lambda p=p: client.rest(f"repos/{repo}/commits?sha={sha}&path={p.strip('/')}&per_page=30"))
                                  for p in paths])
    for path, commits in zip(paths, histories):
        for c in commits:
            for n in _PR_REF.findall(c["commit"]["message"].splitlines()[0]):
                row = found.setdefault(int(n), {"touched": set(), "date": c["commit"]["author"]["date"]})
                row["touched"].add(path)
    return found


def _search_candidates(client, org: Dict[str, Any], repo: str, sha: str, paths: List[str], state_q: str
                       ) -> Dict[int, Dict[str, Any]]:
    """Closed or open PRs that changed these paths: PRs under the paths' area labels, checked file by file."""
    # The repository's path rules match files, so a folder is tried as a file inside it.
    probes = [x for p in paths for x in (p, p.rstrip("/") + "/_")]
    areas = list(rules.area_labels(rules.label_rules_at(client, org, repo, sha), org, probes))
    found: Dict[int, Dict[str, Any]] = {}
    results = client.parallel(*[(lambda a=a: client.graphql(
        CANDIDATES, {"q": f'repo:{repo} is:pr {state_q} label:"{a}" sort:updated-desc'})["search"]["nodes"]) for a in areas]) if areas else []
    for nodes in results:
        for pr in nodes:
            if not pr:
                continue
            files = [n["path"] for n in pr["files"]["nodes"]]
            hit = {p for p in paths for f in files if f == p or f.startswith(p.rstrip("/") + "/")}
            if hit:
                found[pr["number"]] = {"touched": hit, "date": ""}
    return found


def _is_bot(node: Dict[str, Any], bots: List[str]) -> bool:
    a = node.get("author") or {}
    login = (a.get("login") or "").lower()
    return (a.get("__typename") == "Bot" or login.endswith("bot") or login.endswith("[bot]")
            or any(login.startswith(b.lower()) for b in bots))


def _voice(pr: Dict[str, Any], bots: List[str], limit: int = 3) -> List[Dict[str, Any]]:
    """What maintainers (people, not bots) said about this PR, strongest first: a review asking for changes, then
    the closing remark on a PR that was not merged, then review threads and comments."""
    def maintainer(n):
        text = (n["body"] or "").strip()
        # Bot commands (/cherry-pick ...), requests to a bot and one-word replies say nothing about why.
        return (n["authorAssociation"] in MAINTAINER and not _is_bot(n, bots)
                and len(text) >= 25 and not text.startswith(("/", "@copilot")))

    comments = [c for c in pr["comments"]["nodes"] if maintainer(c)]
    items = []
    for r in pr["reviews"]["nodes"]:
        if maintainer(r) and (r["body"] or "").strip():
            items.append((0 if r["state"] == "CHANGES_REQUESTED" else 2, "review", r))
    if comments and not pr["merged"]:
        items.append((1, "closing_remark", comments[-1]))
        comments = comments[:-1]
    for t in pr["reviewThreads"]["nodes"]:
        first = (t["comments"]["nodes"] or [None])[0]
        if first and maintainer(first):
            items.append((3, "review_thread", first))
    items += [(4, "comment", c) for c in comments]
    items.sort(key=lambda x: x[0])
    return [{"by": (n["author"] or {}).get("login"), "kind": kind, **(excerpt(n["body"], n["url"], 300) or {})}
            for _, kind, n in items[:limit] if (n["body"] or "").strip()]


def _discussion(client, repo: str, numbers: List[int]) -> Dict[int, Dict[str, Any]]:
    """The discussion on each PR. GitHub works through the PRs of one query one by one, so they go out as small
    queries at the same time."""
    owner, name = repo.split("/", 1)
    chunks = [numbers[i:i + 3] for i in range(0, len(numbers), 3)]

    def one(chunk):
        body = " ".join(f"p{n}: pullRequest(number: {n}) {{ ...F }}" for n in chunk)
        return client.graphql(DISCUSSION % body, {"owner": owner, "name": name})["repository"]

    out: Dict[int, Dict[str, Any]] = {}
    for chunk, data in zip(chunks, client.parallel(*[(lambda c=c: one(c)) for c in chunks])):
        out.update({n: data[f"p{n}"] for n in chunk if data.get(f"p{n}")})
    return {n: out[n] for n in numbers if n in out}


def related_prs(client, org: Dict[str, Any], repo: str, paths: Optional[List[str]] = None, state: str = "merged",
                limit: int = 5) -> Dict[str, Any]:
    """Earlier PRs that changed these paths, with what the maintainers said about each: the decisions and their reasons.
    merged = the precedents (from the paths' commit history); closed = what was tried and not accepted; open = who is
    working here now. Bot PRs are left out, and so are closed PRs nobody discussed (stale sweeps)."""
    paths = [p for p in (paths or []) if p.strip("/")]
    if not paths:
        return {"success": False, "error": "give the paths (files or folders) to look at"}
    _, sha = client.default_branch_sha(repo)
    if state == "merged":
        found = _merged_candidates(client, repo, sha, paths)
    else:
        found = _search_candidates(client, org, repo, sha, paths, {"open": "is:open", "closed": "is:closed is:unmerged"}[state])
    # Newest and most-overlapping first; fetch a few extra, since bots and undiscussed PRs drop out below.
    ranked = sorted(found, key=lambda n: (len(found[n]["touched"]), found[n]["date"], n), reverse=True)[:limit * 3]
    if not ranked:
        return {"success": True, "repo": repo, "state": state, "count": 0, "prs": [],
                "note": "no earlier PRs found for these paths"}
    bots = org["review_bots"]
    rows = []
    for n, pr in _discussion(client, repo, ranked).items():
        if _is_bot(pr, bots):
            continue
        voice = _voice(pr, bots)
        author = (pr["author"] or {}).get("login")
        discussed = voice or any(not _is_bot(c, bots) and (c["author"] or {}).get("login") != author for c in pr["comments"]["nodes"])
        if state == "closed" and not discussed:
            continue
        reviews = pr["reviews"]["nodes"]
        closer = next((e.get("closer") for e in pr["timelineItems"]["nodes"] if e and e.get("closer")), None)
        rows.append({
            "number": n, "url": pr["url"], "title": pr["title"], "author": author, "labels": label_names(pr)[:8],
            "outcome": "merged" if pr["merged"] else ("open" if pr["state"] == "OPEN" else "closed_unmerged"),
            "date": pr["mergedAt"] or pr["closedAt"],
            "size": {"additions": pr["additions"], "deletions": pr["deletions"], "files": pr["changedFiles"]},
            "paths_touched": sorted(found[n]["touched"]),
            "linked_issues": [{"number": i["number"], "title": i["title"], "url": i["url"]} for i in pr["closingIssuesReferences"]["nodes"]],
            "approvals": sum(1 for r in reviews if r["state"] == "APPROVED" and not _is_bot(r, bots)),
            "changes_requested_by": sorted({(r["author"] or {}).get("login") for r in reviews if r["state"] == "CHANGES_REQUESTED"}),
            "review_threads": pr["reviewThreads"]["totalCount"],
            "superseded_by": closer["url"] if closer else None,
            "maintainer_voice": voice,
        })
    rows.sort(key=lambda r: (len(r["paths_touched"]), bool(r["maintainer_voice"]), r["date"] or ""), reverse=True)
    return {"success": True, "repo": repo, "state": state, "count": len(rows[:limit]), "prs": rows[:limit],
            "note": "maintainer_voice quotes people with owner, member or collaborator rights, not bots; it is evidence "
                    "of what was decided and why, so read the linked thread before relying on a one-line excerpt"
                    + ("; found through the area labels of these paths" if state != "merged" else "")}


# -- my_pr_state ---------------------------------------------------------------------------




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
