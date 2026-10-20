"""issue_history (what happened to an issue before you start) and find_issues (what to work on)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import prs, rules
from .github import excerpt



MAINTAINER_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")

QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number title state url createdAt closedAt
      author { login }
      labels(first: 30) { nodes { name } }
      assignees(first: 10) { nodes { login } }
      comments(last: 30) { nodes { author { login } authorAssociation body createdAt url } }
      timelineItems(first: 100, itemTypes: [CROSS_REFERENCED_EVENT]) {
        nodes {
          ... on CrossReferencedEvent {
            willCloseTarget
            source {
              ... on PullRequest {
                number title url state merged isDraft createdAt updatedAt closedAt mergedAt
                author { login } authorAssociation
                additions deletions changedFiles reviewDecision mergeable
                labels(first: 20) { nodes { name } }
                files(first: 100) { nodes { path } }
                comments(last: 15) { nodes { author { login } authorAssociation body createdAt url } }
                reviews(last: 15) { nodes { author { login } authorAssociation state submittedAt } }
                closedBy: timelineItems(last: 3, itemTypes: [CLOSED_EVENT]) {
                  nodes { ... on ClosedEvent { createdAt actor { login } } }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _parse(ts: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def _days(since: Optional[str], now: datetime) -> Optional[int]:
    d = _parse(since)
    return (now - d).days if d else None


def _name(node: Optional[Dict[str, Any]]) -> Optional[str]:
    return ((node or {}).get("author") or {}).get("login")


def _pr_state(pr: Dict[str, Any]) -> str:
    if pr["merged"]:
        return "merged"
    return "open" if pr["state"] == "OPEN" else "closed_unmerged"


def _close_note(pr: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The last comment by someone other than the PR author, from a maintainer, at or before the
    close. Reported as an excerpt with its link; the reader decides what it means."""
    closed_at = _parse(pr.get("closedAt"))
    author = _name(pr)
    candidates = [c for c in (pr.get("comments") or {}).get("nodes", [])
                  if c.get("authorAssociation") in MAINTAINER_ASSOCIATIONS and _name(c) != author
                  and (closed_at is None or _parse(c["createdAt"]) <= closed_at + timedelta(hours=1))]
    if not candidates:
        return None
    last = max(candidates, key=lambda c: c["createdAt"])
    return {"by": _name(last), "association": last["authorAssociation"], "at": last["createdAt"],
            "url": last["url"], **(excerpt(last.get("body"), last["url"], 500) or {})}


def summarise_pr(pr: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    state = _pr_state(pr)
    labels = [n["name"] for n in (pr.get("labels") or {}).get("nodes", [])]
    paths = [n["path"] for n in (pr.get("files") or {}).get("nodes", [])]
    reviews = (pr.get("reviews") or {}).get("nodes", [])
    closers = [n for n in (pr.get("closedBy") or {}).get("nodes", []) if n]
    closed_by = (closers[-1].get("actor") or {}).get("login") if closers else None
    last_activity = max([pr["updatedAt"]] + [c["createdAt"] for c in (pr.get("comments") or {}).get("nodes", [])])
    return {
        "number": pr["number"], "url": pr["url"], "title": pr["title"], "state": state,
        "is_draft": pr["isDraft"], "author": _name(pr), "author_association": pr.get("authorAssociation"),
        "created_at": pr["createdAt"], "closed_at": pr.get("closedAt"), "merged_at": pr.get("mergedAt"),
        "last_activity_at": last_activity, "days_since_activity": _days(last_activity, now),
        "labels": labels, "will_close_issue": bool(pr.get("_will_close")),
        "closed_by": closed_by if state == "closed_unmerged" else None,
        "closed_by_someone_else": bool(closed_by and closed_by != _name(pr)) if state == "closed_unmerged" else None,
        "close_note": _close_note(pr) if state == "closed_unmerged" else None,
        "approved_by_maintainer": any(r["state"] == "APPROVED" and r.get("authorAssociation") in MAINTAINER_ASSOCIATIONS
                                      for r in reviews),
        "changes_requested": any(r["state"] == "CHANGES_REQUESTED" for r in reviews),
        "merge_conflicts": pr.get("mergeable") == "CONFLICTING" if state == "open" else None,
        "size": {"additions": pr["additions"], "deletions": pr["deletions"], "changed_files": pr["changedFiles"]},
        "touches_tests": any(p.endswith(("_test.go", ".test.ts", "_test.py")) or "/test" in p or "/e2e" in p
                             for p in paths),
        "paths": paths[:30],
    }


def issue_history(client, org: Dict[str, Any], repo: str, number: int, me: Optional[str] = None,
                  inactive_days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    owner, name = repo.split("/", 1)
    issue = client.graphql(QUERY, {"owner": owner, "name": name, "number": number})["repository"]["issue"]
    if issue is None:
        return {"success": False, "error": f"issue #{number} not found in {repo}"}

    seen: Dict[int, Dict[str, Any]] = {}
    for ev in issue["timelineItems"]["nodes"]:
        src = ev.get("source") if ev else None
        if not src or "number" not in src:
            continue          # a cross-reference from an issue, not a PR
        src = {**src, "_will_close": ev.get("willCloseTarget")}
        if src["number"] not in seen or src["_will_close"]:
            seen[src["number"]] = src
    prs = sorted((summarise_pr(p, now) for p in seen.values()), key=lambda p: p["created_at"])

    labels = [n["name"] for n in issue["labels"]["nodes"]]
    assignees = [n["login"] for n in issue["assignees"]["nodes"]]
    maintainer_comments = [
        {"by": _name(c), "association": c["authorAssociation"], "at": c["createdAt"], "url": c["url"],
         **(excerpt(c["body"], c["url"], 400) or {})}
        for c in issue["comments"]["nodes"] if c.get("authorAssociation") in MAINTAINER_ASSOCIATIONS
    ][-5:]

    open_prs = [p for p in prs if p["state"] == "open"]
    attention = set(org["readiness"]["attention_labels"])
    adoptable = []
    for p in prs:
        if p["state"] == "merged":
            continue
        stale = p["state"] == "open" and (attention & set(p["labels"]) or (p["days_since_activity"] or 0) >= inactive_days)
        unmerged_with_work = p["state"] == "closed_unmerged" and (p["approved_by_maintainer"] or p["touches_tests"])
        if stale or unmerged_with_work:
            adoptable.append({
                "number": p["number"], "url": p["url"], "state": p["state"],
                "why": ("open and inactive or flagged: " + ", ".join(sorted(attention & set(p["labels"])) or
                                                                 [f"{p['days_since_activity']} days idle"]))
                       if stale else "closed unmerged but has maintainer approval or tests",
                "done": {"size": p["size"], "touches_tests": p["touches_tests"],
                         "approved_by_maintainer": p["approved_by_maintainer"],
                         "merge_conflicts": p["merge_conflicts"]},
                "note": "check close_note before adopting; the author or a maintainer may have said why",
            })

    src = issue["url"]
    signals: List[Dict[str, Any]] = []

    def sig(kind: str, detail: str, url: str = src) -> None:
        signals.append({"signal": kind, "detail": detail, "source": url})

    if issue["state"] == "CLOSED":
        sig("issue_closed", f"closed {issue['closedAt']}")
    for p in open_prs:
        sig("open_pr_exists", f"#{p['number']} by {p['author']} ({p['days_since_activity']} days since activity)", p["url"])
    closed = [p for p in prs if p["state"] == "closed_unmerged"]
    if closed:
        sig("earlier_prs_closed_without_merge", ", ".join(f"#{p['number']}" for p in closed))
    for p in closed:
        if p["close_note"]:
            sig("maintainer_note_on_close", f"#{p['number']}: see close_note", p["close_note"]["url"])
    if assignees:
        others = [a for a in assignees if a != me]
        if others:
            sig("assigned_to_someone_else", ", ".join(others))
        if me in assignees:
            sig("assigned_to_you", me)
    if not assignees:
        sig("unassigned", "no assignee")
    entry = [l for l in labels if l in org["entry_labels"]]
    if entry:
        sig("entry_level_label", ", ".join(entry))
    reserved = [l for l in labels if l in org.get("reserved_labels", [])]
    if reserved:
        sig("reserved_for_a_program", ", ".join(reserved) + ": the maintainers run this program with its own rules; do not start without it")
    if not maintainer_comments:
        sig("no_maintainer_comment", "no comment from an owner, member or collaborator")

    return {"success": True, "repo": repo,
            "issue": {"number": issue["number"], "title": issue["title"], "state": issue["state"], "url": src,
                      "labels": labels, "assignees": assignees, "created_at": issue["createdAt"]},
            "prs": prs, "maintainer_comments": maintainer_comments, "adoptable": adoptable,
            "signals": signals,
            "note": "signals are facts with sources; the decision on whether to start is the contributor's"}


ISSUE_SEARCH = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 50) {
    nodes {
      ... on Issue {
        number title url createdAt updatedAt
        labels(first: 20) { nodes { name } }
        assignees(first: 5) { nodes { login } }
        comments { totalCount }
        timelineItems(first: 50, itemTypes: [CROSS_REFERENCED_EVENT]) {
          nodes { ... on CrossReferencedEvent { source { ... on PullRequest { number state merged url } } } }
        }
      }
    }
  }
}
"""


def _topic(area: str, prefixes: List[str]) -> str:
    for p in prefixes:
        if area.startswith(p):
            area = area[len(p):]
    return area.lower()


def find_issues(client, org: Dict[str, Any], repo: str, known_areas: Optional[List[str]] = None,
                labels: Optional[List[str]] = None, limit: int = 15) -> Dict[str, Any]:
    """Open issues carrying an entry-level label, minus the labels the organisation reserves for other programs.
    Tier: `free` (no assignee, no open PR), `claimed` (assigned but no open PR: check how active the assignee is),
    `has_open_pr`. Known areas rank first inside a tier; an issue matches an area when one of its labels equals or
    contains the area's name (`area/mutation` matches the issue label `mutation`)."""
    entry = labels or org["entry_labels"]
    topics = {_topic(a, org["labels"]["area_prefixes"]): a for a in (known_areas or [])}
    include = ",".join(f'"{l}"' for l in entry)
    exclude = " ".join(f'-label:"{l}"' for l in org.get("reserved_labels", []))
    base = f"repo:{repo} is:issue is:open label:{include} {exclude} sort:updated-desc"
    # Most entry-level issues are assigned, so unassigned ones would be crowded out of one search's 50 results.
    found: Dict[int, Dict[str, Any]] = {}
    for q in (base + " no:assignee", base):
        for n in client.graphql(ISSUE_SEARCH, {"q": q})["search"]["nodes"]:
            if n:
                found.setdefault(n["number"], n)
    rows = []
    for n in found.values():
        linked = [e["source"] for e in n["timelineItems"]["nodes"] if e and e.get("source") and "number" in e["source"]]
        open_prs = [p for p in linked if p["state"] == "OPEN"]
        closed = [p for p in linked if p["state"] == "CLOSED" and not p["merged"]]
        names = prs.label_names(n)
        matched = sorted({topics[t] for t in topics for l in names if l.lower() == t or t in l.lower()})
        rows.append({
            "number": n["number"], "url": n["url"], "title": n["title"], "labels": names,
            "created_at": n["createdAt"], "updated_at": n["updatedAt"], "comments": n["comments"]["totalCount"],
            "matches_known_areas": matched, "open_prs": [{"number": p["number"], "url": p["url"]} for p in open_prs],
            "closed_unmerged_prs": len(closed), "assignees": [a["login"] for a in n["assignees"]["nodes"]],
            "tier": "has_open_pr" if open_prs else ("claimed" if n["assignees"]["nodes"] else "free"),
        })
    order = {"free": 0, "claimed": 1, "has_open_pr": 2}
    rows.sort(key=lambda r: (order[r["tier"]], not r["matches_known_areas"], r["closed_unmerged_prs"]))
    return {"success": True, "repo": repo, "count": min(len(rows), limit), "issues": rows[:limit],
            "note": "claimed and has_open_pr issues may still be workable (collaborate, or the claim is idle): run issue_history before recommending any"}
