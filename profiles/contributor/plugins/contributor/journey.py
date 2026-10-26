"""The contributor's journey: private local notes (this machine only) and a record computed from GitHub (what they
merged, reviewed and discussed, in this repository and across the organisation), their stage, review opportunities
in the areas they know, and progress against the organisation's role ladder."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import rules
from .github import hermes_home

NOTE_KINDS = ("area_known", "struggle", "review_feedback", "in_flight", "retro", "self_reported")
SELF_REPORTED = ("slack_member", "attended_meeting", "mailing_list")
MAX_NOTES = 500
MAX_TEXT = 300


def base_dir() -> Path:
    return Path(os.environ.get("KYVERNO_CONTRIBUTOR_HOME") or hermes_home() / "kyverno-contributor")


def path() -> Path:
    return base_dir() / "journey.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty() -> Dict[str, Any]:
    return {"notes": [], "self_reported": {}, "updated_at": None}


def load() -> Dict[str, Any]:
    try:
        with open(path(), encoding="utf-8") as f:
            return {**empty(), **json.load(f)}
    except (OSError, ValueError):
        return empty()


@contextmanager
def _locked() -> Iterator[None]:
    base_dir().mkdir(parents=True, exist_ok=True)
    with open(base_dir() / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _save(state: Dict[str, Any]) -> None:
    state["updated_at"] = now()
    fd, tmp = tempfile.mkstemp(dir=base_dir(), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path())


def add_note(kind: str, text: str, area: str = "", ref: str = "") -> Dict[str, Any]:
    if kind not in NOTE_KINDS or kind == "self_reported":
        raise ValueError(f"kind must be one of {[k for k in NOTE_KINDS if k != 'self_reported']}")
    text = " ".join(str(text).split())[:MAX_TEXT]
    if not text:
        raise ValueError("text is required")
    note = {"kind": kind, "text": text, "area": area[:80], "ref": ref[:200], "at": now()}
    with _locked():
        state = load()
        state["notes"] = (state["notes"] + [note])[-MAX_NOTES:]
        _save(state)
    return note


def set_self_reported(item: str, value: bool, on: str = "") -> None:
    if item not in SELF_REPORTED:
        raise ValueError(f"item must be one of {list(SELF_REPORTED)}")
    with _locked():
        state = load()
        state["self_reported"][item] = {"value": bool(value), "on": on[:40] or now()[:10]}
        _save(state)


# -- summary ---------------------------------------------------------------------------------



SUMMARY_QUERY = """
query($merged: String!, $open: String!, $reviewed: String!, $issues: String!, $prs: String!,
      $here_merged: String!, $here_closed: String!, $here_reviewed: String!,
      $here_authored: String!, $here_commented: String!, $candidates: String!) {
  merged: search(query: $merged, type: ISSUE, first: 1) { issueCount }
  open: search(query: $open, type: ISSUE, first: 20) {
    nodes { ... on PullRequest { number title url updatedAt repository { nameWithOwner } } }
  }
  reviewed: search(query: $reviewed, type: ISSUE, first: 1) { issueCount }
  issue_comments: search(query: $issues, type: ISSUE, first: 1) { issueCount }
  pr_comments: search(query: $prs, type: ISSUE, first: 1) { issueCount }
  here_merged: search(query: $here_merged, type: ISSUE, first: 30) {
    issueCount
    nodes { ... on PullRequest { number title url mergedAt labels(first: 20) { nodes { name } }
      files(first: 50) { nodes { path } } closingIssuesReferences(first: 5) { nodes { number } } } }
  }
  here_closed: search(query: $here_closed, type: ISSUE, first: 10) {
    issueCount
    nodes { ... on PullRequest { number title url closedAt } }
  }
  here_reviewed: search(query: $here_reviewed, type: ISSUE, first: 20) {
    issueCount
    nodes { ... on PullRequest { number title url labels(first: 20) { nodes { name } } files(first: 50) { nodes { path } } } }
  }
  here_authored: search(query: $here_authored, type: ISSUE, first: 10) {
    issueCount
    nodes { ... on Issue { number title url state } }
  }
  here_commented: search(query: $here_commented, type: ISSUE, first: 10) {
    issueCount
    nodes { ... on Issue { number title url state } }
  }
  candidates: search(query: $candidates, type: ISSUE, first: 30) {
    issueCount
    nodes { ... on PullRequest { number title url updatedAt reviewDecision author { login __typename }
      files(first: 50) { nodes { path } } } }
  }
}
"""

TOUCH_DEPTH = 3


def _dirs(pr: Dict[str, Any], noise: List[str]) -> set:
    """Directories (to TOUCH_DEPTH levels) a PR touched, leaving out generated and bookkeeping files."""
    found = set()
    for n in (pr.get("files") or {}).get("nodes", []):
        path = n["path"]
        if any(x in path for x in noise) or "/" not in path:
            continue
        found.add("/".join(path.split("/")[:-1][:TOUCH_DEPTH]))
    return found


def _area_counts(prs: List[Dict[str, Any]], label_rules: Dict[str, Any], org: Dict[str, Any]) -> Counter:
    counts: Counter = Counter()
    prefixes = tuple(org["labels"]["area_prefixes"])
    for pr in prs:
        areas = {n["name"] for n in pr["labels"]["nodes"] if n["name"].startswith(prefixes)}
        if not areas and "files" in pr:        # PRs without labels: apply the path rules
            areas = set(rules.area_labels(label_rules, org, [n["path"] for n in pr["files"]["nodes"]]))
        counts.update(a for a in areas if a.startswith(prefixes))
    return counts


def ladder_progress(org: Dict[str, Any], merged: int, collaboration: int, reviews: int,
                    self_reported: Dict[str, Any], governance_blob_sha: Optional[str]) -> Dict[str, Any]:
    ladder = org["ladder"]
    need = ladder["roles"]["contributor"]["needs"]
    verified = ladder.get("verified_blob_sha")
    criteria = ("unverified: criteria in org.yaml have not been checked against the source"
                if not verified else "verified" if verified == governance_blob_sha
                else "source changed since org.yaml was checked: re-read it")
    reported = {k: (self_reported.get(k) or {}).get("value") for k in ladder["roles"]["contributor"]["self_reported"]}
    merged_ok = merged >= need["merged_prs"]
    return {
        "source": f"https://github.com/{ladder['source_repo']}/blob/main/{ladder['source_path']}",
        "criteria_status": criteria,
        "contributor": {
            "merged_prs": {"have": merged, "need": need["merged_prs"], "met": merged_ok},
            "total_contributions": {"need": need["total_contributions"],
                                    "counted_from_github": merged + 0,
                                    "possible_collaboration_items": collaboration,
                                    "note": "the project counts extensive collaboration, blog posts and Slack support; "
                                            "only the contributor can say which of their items qualify"},
            "self_reported": reported,
            "still_to_confirm": [k for k, v in reported.items() if not v],
            "recognition": ladder["roles"]["contributor"]["recognition"],
        },
        "reviewer": {"reviews_on_prs_by_others": reviews, "signals": ladder["roles"]["reviewer"]["signals"],
                     "note": "reviewers are appointed or nominated by a maintainer; this is evidence, not eligibility"},
    }


def _stage(merged: int, need: int) -> str:
    return "first_time" if merged == 0 else "early" if merged < need else "contributor"


def _days(iso: str) -> int:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days


def journey_summary(client, org: Dict[str, Any], repo: str, login: str) -> Dict[str, Any]:
    """What the contributor has done on GitHub, centred on `repo` (their target) with organisation-wide totals for
    the ladder, plus the open PRs by others that touch what they know (once they have a merged PR)."""
    ladder = org["ladder"]
    scope = f"org:{ladder['org']} " + " ".join(f"-repo:{r}" for r in ladder.get("ignore_repos", []))
    here = f"repo:{repo}"
    data = client.graphql(SUMMARY_QUERY, {
        "merged": f"{scope} author:{login} is:pr is:merged",
        "open": f"{scope} author:{login} is:pr is:open sort:updated-desc",
        "reviewed": f"{scope} reviewed-by:{login} -author:{login} is:pr",
        "issues": f"{scope} commenter:{login} -author:{login} is:issue",
        "prs": f"{scope} commenter:{login} -author:{login} is:pr",
        "here_merged": f"{here} author:{login} is:pr is:merged sort:updated-desc",
        "here_closed": f"{here} author:{login} is:pr is:closed is:unmerged sort:updated-desc",
        "here_reviewed": f"{here} reviewed-by:{login} -author:{login} is:pr sort:updated-desc",
        "here_authored": f"{here} author:{login} is:issue sort:updated-desc",
        "here_commented": f"{here} commenter:{login} -author:{login} is:issue sort:updated-desc",
        "candidates": f"{here} is:pr is:open draft:false -author:{login} -reviewed-by:{login} sort:updated-desc",
    })
    _, sha = client.default_branch_sha(repo)
    label_rules = rules.label_rules_at(client, org, repo, sha)
    noise = org.get("noise_paths", [])
    merged_here = [n for n in data["here_merged"]["nodes"] if n]
    reviewed_here = [n for n in data["here_reviewed"]["nodes"] if n]
    areas = _area_counts(merged_here, label_rules, org)
    reviewed_areas = _area_counts(reviewed_here, label_rules, org)
    touched: Counter = Counter()
    for pr in merged_here:
        touched.update(_dirs(pr, noise))
    store = load()
    try:
        blob = client.rest(f"repos/{ladder['source_repo']}/contents/{ladder['source_path']}").get("sha")
    except Exception:
        blob = None
    merged_total = data["merged"]["issueCount"]
    need = ladder["roles"]["contributor"]["needs"]["merged_prs"]
    stage = _stage(merged_total, need)

    opportunities: List[Dict[str, Any]] = []
    if merged_total >= 1 and touched:     # after the first merged PR: review others' work on what they know
        for pr in (n for n in data["candidates"]["nodes"] if n):
            if (pr.get("author") or {}).get("__typename") == "Bot" or pr.get("reviewDecision") == "APPROVED":
                continue          # bots and already-approved PRs need a human review least
            shared = sorted(_dirs(pr, noise) & set(touched))
            if shared:
                opportunities.append({
                    "number": pr["number"], "title": pr["title"], "url": pr["url"],
                    "author": (pr.get("author") or {}).get("login"), "days_since_update": _days(pr["updatedAt"]),
                    "review_decision": pr.get("reviewDecision"), "shared_paths": shared[:3],
                    "_score": sum(touched[d] for d in shared)})
        opportunities.sort(key=lambda o: -o["_score"])
        for o in opportunities:
            del o["_score"]

    notes = store["notes"]
    return {
        "success": True, "login": login, "repo": repo, "stage": stage,
        "stage_note": {"first_time": "no merged PR yet in the organisation: help them land the first one; do not suggest reviewing others yet",
                       "early": "some merged PRs: reviewing PRs in areas they know is a good next step",
                       "contributor": "meets the merged-PR count: reviewing others' work is the path toward reviewer"}[stage],
        "in_this_repo": {
            "merged_prs": {"total": data["here_merged"]["issueCount"],
                           "recent": [{"number": n["number"], "title": n["title"], "url": n["url"], "merged_at": n["mergedAt"],
                                       "closes_issues": [i["number"] for i in n["closingIssuesReferences"]["nodes"]]}
                                      for n in merged_here[:10]]},
            "closed_without_merge": [{"number": n["number"], "title": n["title"], "url": n["url"], "closed_at": n["closedAt"]}
                                     for n in data["here_closed"]["nodes"] if n],
            "reviewed_prs": {"total": data["here_reviewed"]["issueCount"],
                             "recent": [{"number": n["number"], "title": n["title"], "url": n["url"]} for n in reviewed_here[:5]]},
            "issues_authored": [n for n in data["here_authored"]["nodes"] if n],
            "issues_commented_on": [n for n in data["here_commented"]["nodes"] if n],
            "touched_paths": [{"path": d, "merged_prs": c} for d, c in touched.most_common(12)],
        },
        "known_areas": [{"area": a, "merged_prs": c} for a, c in areas.most_common()],
        "reviewed_areas": [{"area": a, "reviews": c} for a, c in reviewed_areas.most_common()],
        "open_prs": [{"number": n["number"], "repo": n["repository"]["nameWithOwner"], "title": n["title"], "url": n["url"],
                      "updated_at": n["updatedAt"]} for n in data["open"]["nodes"] if n],
        "review_opportunities": {
            "prs": opportunities[:5],
            "note": "open, not yet approved PRs by others, with no review from the contributor, that touch paths they changed before"},
        "organisation_totals": {"merged_prs": merged_total, "reviews_on_others_prs": data["reviewed"]["issueCount"],
                                "comments_on_others_threads": {"issues": data["issue_comments"]["issueCount"],
                                                               "prs": data["pr_comments"]["issueCount"]}},
        "ladder": ladder_progress(org, merged_total,
                                  data["issue_comments"]["issueCount"] + data["pr_comments"]["issueCount"],
                                  data["reviewed"]["issueCount"], store["self_reported"], blob),
        "notes": {k: [n for n in notes if n["kind"] == k][-5:] for k in ("area_known", "struggle", "review_feedback", "in_flight", "retro")},
        "scope_note": f"'in_this_repo' covers {repo}; totals cover org:{ladder['org']} without {ladder.get('ignore_repos', [])}; "
                      "GitHub search lists the most recent results only",
    }
