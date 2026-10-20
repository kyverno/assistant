"""Background change detection for the contributor's PRs, issues and discussions.

main() is the model-free scheduled check (run through scripts/kyverno-watch.py by `hermes cron --no-agent`):
it prints one line per new event and appends them to a local inbox. read_inbox() shows the inbox at the
start of a session. The first run only records a baseline.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from typing import Any, Dict, List, Optional

from . import github, journey, rules

QUERY = """
query($mine: String!, $merged: String!, $mentions: String!, $review: String!, $disc: String!, $assigned: String!) {
  mine: search(query: $mine, type: ISSUE, first: 30) { nodes { ... on PullRequest {
    number url title repository { nameWithOwner } updatedAt mergeable
    labels(first: 30) { nodes { name } }
    reviews(last: 10) { nodes { author { login } authorAssociation state submittedAt } }
    comments(last: 10) { nodes { author { login } createdAt } }
    reviewThreads(first: 100) { nodes { isResolved } }
    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
  } } }
  merged: search(query: $merged, type: ISSUE, first: 10) { nodes { ... on PullRequest {
    number url title repository { nameWithOwner } mergedAt } } }
  mentions: search(query: $mentions, type: ISSUE, first: 20) { nodes {
    ... on PullRequest { number url title repository { nameWithOwner } }
    ... on Issue { number url title repository { nameWithOwner } } } }
  review_requested: search(query: $review, type: ISSUE, first: 20) { nodes { ... on PullRequest {
    number url title repository { nameWithOwner } } } }
  discussions: search(query: $disc, type: DISCUSSION, first: 20) { nodes { ... on Discussion {
    number url title repository { nameWithOwner } comments { totalCount } } } }
  assigned: search(query: $assigned, type: ISSUE, first: 20) { nodes { ... on Issue {
    number url title repository { nameWithOwner } comments { totalCount } labels(first: 20) { nodes { name } } } } }
}
"""

BOT_HINTS = ("copilot", "coderabbit", "[bot]")


def variables(login: str, scope: str) -> Dict[str, str]:
    return {
        "mine": f"{scope} author:{login} is:pr is:open",
        "merged": f"{scope} author:{login} is:pr is:merged sort:updated-desc",
        "mentions": f"{scope} mentions:{login} -author:{login} is:open",
        "review": f"{scope} review-requested:{login} is:pr is:open",
        "disc": f"{scope} involves:{login}",
        "assigned": f"{scope} assignee:{login} is:issue is:open",
    }


def _key(n: Dict[str, Any]) -> str:
    return f"{n['repository']['nameWithOwner']}#{n['number']}"


def _is_bot(login: str) -> bool:
    return any(h in login.lower() for h in BOT_HINTS)


def snapshot(data: Dict[str, Any], login: str) -> Dict[str, Any]:
    """Reduce the GraphQL response to the small state the diff needs."""
    prs = {}
    for n in (x for x in data["mine"]["nodes"] if x):
        others = [r for r in n["reviews"]["nodes"] if (r["author"] or {}).get("login") not in (login, None)]
        comments = [c for c in n["comments"]["nodes"] if (c["author"] or {}).get("login") not in (login, None)]
        roll = n["commits"]["nodes"][0]["commit"]["statusCheckRollup"] if n["commits"]["nodes"] else None
        prs[_key(n)] = {
            "title": n["title"], "url": n["url"], "labels": sorted(l["name"] for l in n["labels"]["nodes"]),
            "mergeable": n["mergeable"], "checks": (roll or {}).get("state"),
            "unresolved": sum(1 for t in n["reviewThreads"]["nodes"] if not t["isResolved"]),
            "last_review_at": max([r["submittedAt"] for r in others if r["submittedAt"]] or [""]),
            "last_comment_at": max([c["createdAt"] for c in comments] or [""]),
            "review_bot_last": max([r["submittedAt"] for r in others
                                    if r["submittedAt"] and _is_bot(r["author"]["login"])] or [""]),
        }
    return {
        "prs": prs,
        "merged": {_key(n): n["mergedAt"] for n in data["merged"]["nodes"] if n},
        "mentions": {_key(n): n["title"] for n in data["mentions"]["nodes"] if n},
        "review_requested": {_key(n): n["title"] for n in data["review_requested"]["nodes"] if n},
        "discussions": {_key(n): {"comments": n["comments"]["totalCount"], "title": n["title"], "url": n["url"]}
                        for n in data["discussions"]["nodes"] if n},
        "assigned": {_key(n): {"comments": n["comments"]["totalCount"], "title": n["title"], "url": n["url"],
                               "labels": sorted(l["name"] for l in n["labels"]["nodes"])}
                     for n in data["assigned"]["nodes"] if n},
        "urls": {**{_key(n): n["url"] for n in data["mentions"]["nodes"] if n},
                 **{_key(n): n["url"] for n in data["review_requested"]["nodes"] if n},
                 **{_key(n): n["url"] for n in data["merged"]["nodes"] if n}},
    }


def diff(old: Optional[Dict[str, Any]], new: Dict[str, Any], attention: List[str]) -> List[Dict[str, str]]:
    """Events between two snapshots. No old snapshot means a baseline run: no events."""
    if old is None:
        return []
    ev: List[Dict[str, str]] = []

    def add(kind: str, key: str, text: str, url: str = "") -> None:
        ev.append({"kind": kind, "key": key, "text": text, "url": url})

    for key, cur in new["prs"].items():
        prev = old["prs"].get(key)
        if prev is None:
            continue
        for label in attention:
            if label in cur["labels"] and label not in prev["labels"]:
                add(label.replace("-", "_"), key, f"{key} got the label '{label}'", cur["url"])
        if cur["mergeable"] == "CONFLICTING" and prev["mergeable"] == "MERGEABLE":   # UNKNOWN is GitHub still computing
            add("merge_conflict", key, f"{key} now has merge conflicts", cur["url"])
        if cur["checks"] in ("FAILURE", "ERROR") and prev["checks"] not in ("FAILURE", "ERROR"):
            add("check_failed", key, f"a check started failing on {key}", cur["url"])
        if cur["last_review_at"] > prev["last_review_at"]:
            bot = cur["review_bot_last"] > prev["review_bot_last"]
            add("new_review", key, f"new {'bot ' if bot else ''}review on {key}", cur["url"])
        if cur["last_comment_at"] > prev["last_comment_at"]:
            add("new_comment", key, f"new comment on {key}", cur["url"])
    for key, merged_at in new["merged"].items():
        if key not in old["merged"] and key in old["prs"]:
            add("merged", key, f"{key} was merged", new["urls"].get(key, ""))
    for kind, noun in (("mentions", "mentioned in"), ("review_requested", "review requested on")):
        for key, title in new[kind].items():
            if key not in old[kind]:
                add(kind[:-1] if kind == "mentions" else kind, key, f"{noun} {key}: {title}", new["urls"].get(key, ""))
    for key, cur in new["discussions"].items():
        prev = old["discussions"].get(key)
        if prev and cur["comments"] > prev["comments"]:
            add("discussion_reply", key, f"new reply in discussion {key}: {cur['title']}", cur["url"])
    for key, cur in new["assigned"].items():
        prev = old["assigned"].get(key)
        if prev and (cur["comments"] > prev["comments"] or cur["labels"] != prev["labels"]):
            add("issue_changed", key, f"your issue {key} changed", cur["url"])
    return ev


# -- scheduled check -------------------------------------------------------------------------


def notify(title: str, body: str) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e", f'display notification {json.dumps(body)} with title {json.dumps(title)}'],
                           check=False, timeout=10)
        else:
            subprocess.run(["notify-send", title, body], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv) -> int:
    login = github.secret("CONTRIBUTOR_GITHUB_LOGIN")
    if not login:
        print("watch: CONTRIBUTOR_GITHUB_LOGIN is not set", file=sys.stderr)
        return 1
    org = rules.load_org()
    base = journey.base_dir()
    base.mkdir(parents=True, exist_ok=True)
    state_file, inbox = base / "watch_state.json", base / "inbox.jsonl"
    try:
        client = github.Client(cache_dir=base / "etags")
        data = client.graphql(QUERY, variables(login, f"org:{org['ladder']['org']}"))
    except github.GitHubError as exc:
        print(f"watch: {exc}", file=sys.stderr)
        return 1
    new = snapshot(data, login)
    try:
        old = json.loads(state_file.read_text())
    except (OSError, ValueError):
        old = None
    found = diff(old, new, org["readiness"]["attention_labels"])
    if "--dry" not in argv:
        state_file.write_text(json.dumps(new))
        if found:
            now = journey.now()
            with open(inbox, "a") as f:
                for e in found:
                    f.write(json.dumps({**e, "at": now, "read": False}) + "\n")
    for e in found:
        print(f"{e['text']} {e['url']}".strip())
    if found and "--notify" in argv:
        notify("Kyverno contributor", f"{len(found)} update(s), first: {found[0]['text']}")
    if old is None:
        print(f"watch: baseline saved ({len(new['prs'])} open PRs); later runs report changes", file=sys.stderr)
    return 0


def read_inbox(mark_read: bool = True, limit: int = 30) -> Dict[str, Any]:
    path = journey.base_dir() / "inbox.jsonl"
    try:
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except OSError:
        rows = []
    unread = [r for r in rows if not r.get("read")]
    if mark_read and unread:
        path.write_text("".join(json.dumps({**r, "read": True}) + "\n" for r in rows[-500:]))
    started = (journey.base_dir() / "watch_state.json").exists()
    return {"success": True, "unread": len(unread), "events": unread[-limit:],
            "watch": "running" if started else "not started: no baseline yet (enable the watch job or run scripts/kyverno-watch.py)"}
