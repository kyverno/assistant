"""The contributor's journey: private local notes (this machine only) and a summary computed from GitHub
(merged PRs, areas, reviews) with progress against the organisation's role ladder."""

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
query($merged: String!, $open: String!, $reviewed: String!, $issues: String!, $prs: String!) {
  merged: search(query: $merged, type: ISSUE, first: 50) {
    issueCount
    nodes { ... on PullRequest { number title url mergedAt repository { nameWithOwner }
      labels(first: 20) { nodes { name } } files(first: 50) { nodes { path } } } }
  }
  open: search(query: $open, type: ISSUE, first: 20) {
    nodes { ... on PullRequest { number title url updatedAt repository { nameWithOwner }
      labels(first: 20) { nodes { name } } } }
  }
  reviewed: search(query: $reviewed, type: ISSUE, first: 30) {
    issueCount
    nodes { ... on PullRequest { number url repository { nameWithOwner } labels(first: 20) { nodes { name } } } }
  }
  issue_comments: search(query: $issues, type: ISSUE, first: 1) { issueCount }
  pr_comments: search(query: $prs, type: ISSUE, first: 1) { issueCount }
}
"""


def _area_counts(prs: List[Dict[str, Any]], label_rules: Dict[str, Any], org: Dict[str, Any]) -> Counter:
    counts: Counter = Counter()
    prefixes = tuple(org["labels"]["area_prefixes"])
    for pr in prs:
        areas = {n["name"] for n in pr["labels"]["nodes"] if n["name"].startswith(prefixes)}
        if not areas and "files" in pr:        # older PRs without labels: apply the path rules
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


def journey_summary(client, org: Dict[str, Any], repo: str, login: str) -> Dict[str, Any]:
    scope = f"org:{org['ladder']['org']}"
    data = client.graphql(SUMMARY_QUERY, {
        "merged": f"{scope} author:{login} is:pr is:merged sort:updated-desc",
        "open": f"{scope} author:{login} is:pr is:open sort:updated-desc",
        "reviewed": f"{scope} reviewed-by:{login} -author:{login} is:pr sort:updated-desc",
        "issues": f"{scope} commenter:{login} -author:{login} is:issue",
        "prs": f"{scope} commenter:{login} -author:{login} is:pr",
    })
    _, sha = client.default_branch_sha(repo)
    label_rules = rules.label_rules_at(client, org, repo, sha)
    merged_nodes = [n for n in data["merged"]["nodes"] if n]
    areas = _area_counts(merged_nodes, label_rules, org)
    reviewed_areas = _area_counts([n for n in data["reviewed"]["nodes"] if n], label_rules, org)
    store = load()
    ladder = org["ladder"]
    try:
        blob = client.rest(f"repos/{ladder['source_repo']}/contents/{ladder['source_path']}").get("sha")
    except Exception:
        blob = None
    merged_total = data["merged"]["issueCount"]
    notes = store["notes"]
    return {
        "success": True, "login": login,
        "merged_prs": {"total": merged_total,
                       "recent": [{"number": n["number"], "repo": n["repository"]["nameWithOwner"], "title": n["title"],
                                   "url": n["url"], "merged_at": n["mergedAt"]} for n in merged_nodes[:10]]},
        "open_prs": [{"number": n["number"], "repo": n["repository"]["nameWithOwner"], "title": n["title"], "url": n["url"],
                      "updated_at": n["updatedAt"]} for n in data["open"]["nodes"] if n],
        "known_areas": [{"area": a, "merged_prs": c} for a, c in areas.most_common()],
        "reviewed_areas": [{"area": a, "reviews": c} for a, c in reviewed_areas.most_common()],
        "reviews_on_others_prs": data["reviewed"]["issueCount"],
        "comments_on_others_threads": {"issues": data["issue_comments"]["issueCount"], "prs": data["pr_comments"]["issueCount"]},
        "ladder": ladder_progress(org, merged_total,
                                  data["issue_comments"]["issueCount"] + data["pr_comments"]["issueCount"],
                                  data["reviewed"]["issueCount"], store["self_reported"], blob),
        "notes": {k: [n for n in notes if n["kind"] == k][-5:] for k in ("area_known", "struggle", "review_feedback", "in_flight", "retro")},
        "scope_note": f"counts cover {scope}; GitHub search caps lists at the most recent results",
    }
