#!/usr/bin/env python3
"""Entry point for the shared issue management: labels, and in one case closes, issues that are resolved or
waiting on their reporter.

The rules, configured in the repository's issue-management.json (every key is optional):

  possibly-resolved    A merged PR references the issue without closing it and nobody commented since the
                       merge. Labels and comments; never closes.
  waiting-on-reporter  An issue with a needs-info label and no reply from its author. Warns with `stale`, then
                       closes as not planned. The only rule that closes.
  idle-untriaged       An untriaged issue with no priority and no human activity for a long time. Labels with
                       `stale` and comments; never closes. Off unless triage_label is set.

Any human comment after a managed label removes it. Exempt labels and milestoned issues are never touched. The
script stores no state: labels and timelines are the state.

Dry-run unless DRY_RUN is the string false. Prints a Markdown report on stdout.
Exit codes: 0 on success, 1 on an error.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

DEFAULTS = {
    "stale_label": "stale",
    "resolved_label": "possibly-resolved",
    # Empty disables the idle-untriaged rule, so only repositories that triage with a label opt in.
    "triage_label": "",
    "priority_prefix": "",
    "needs_info_labels": ["needs-info"],
    "exempt_labels": ["no-stale", "hold", "security"],
    "warning_days": 14,
    "close_days": 14,
    "idle_days": 180,
    "resolved_grace_days": 3,
    "warning_comment": (
        "This issue is waiting on more information from the reporter and has had no reply. It will be closed in "
        "{days} days unless there is a response. Any comment removes the `stale` label."
    ),
    "close_comment": (
        "Closing as not planned: no reply from the reporter for {days} days. Please reopen with the requested "
        "information if this is still a problem."
    ),
    "resolved_comment": (
        "A merged PR (#{pr}) references this issue but did not close it, and nothing has been said since. Is this "
        "resolved? A maintainer or the reporter can close it, or comment to remove the `possibly-resolved` label."
    ),
    "idle_comment": (
        "This issue has had no activity for {days} days and has not been triaged. A maintainer should set a "
        "priority or close it. Any comment removes the `stale` label."
    ),
}
LIST_KEYS = {"needs_info_labels", "exempt_labels"}
DAY_KEYS = {"warning_days", "close_days", "idle_days", "resolved_grace_days"}
COMMENT_FIELDS = {
    "warning_comment": {"days"},
    "close_comment": {"days"},
    "resolved_comment": {"pr"},
    "idle_comment": {"days"},
}
LABEL_COLORS = {"stale": "CFD3D7", "possibly-resolved": "0E8A16"}

QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 50, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        createdAt
        author { login __typename }
        labels(first: 30) { nodes { name } }
        milestone { title }
        comments(last: 10) { nodes { createdAt author { login __typename } } }
        timelineItems(last: 100, itemTypes: [LABELED_EVENT, CROSS_REFERENCED_EVENT]) {
          nodes {
            __typename
            ... on LabeledEvent { createdAt label { name } }
            ... on CrossReferencedEvent {
              willCloseTarget
              source { ... on PullRequest { number merged mergedAt } }
            }
          }
        }
      }
    }
  }
}
"""


def log(message):
    print(f"[issue-management] {message}", file=sys.stderr)


def load_config(path):
    """Merges the repository's config over DEFAULTS, rejecting unknown keys and bad values."""
    config = dict(DEFAULTS)
    if path.exists():
        given = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(given, dict):
            raise ValueError("config must be a JSON object")
        unknown = set(given) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
        config.update(given)
    for key in LIST_KEYS:
        if not isinstance(config[key], list) or not all(isinstance(v, str) and v for v in config[key]):
            raise ValueError(f"{key} must be a list of label names")
    for key in DAY_KEYS:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3650:
            raise ValueError(f"{key} must be a whole number of days from 1 to 3650")
    for key in ("stale_label", "resolved_label", "triage_label", "priority_prefix"):
        if not isinstance(config[key], str):
            raise ValueError(f"{key} must be a string")
    if not config["stale_label"] or not config["resolved_label"]:
        raise ValueError("stale_label and resolved_label must not be empty")
    for key, fields in COMMENT_FIELDS.items():
        if set(re.findall(r"{(\w*)}", config[key])) - fields:
            raise ValueError(f"{key} may only use {', '.join('{' + f + '}' for f in sorted(fields))}")
        config[key].format(**dict.fromkeys(fields, 0))
    return config


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_bot(author):
    return author is None or author["__typename"] == "Bot" or author["login"].endswith("[bot]")


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def fetch_issues(repo):
    owner, name = repo.split("/")
    issues, after = [], None
    while True:
        args = ["api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
        if after:
            args += ["-F", f"after={after}"]
        page = json.loads(gh(*args))["data"]["repository"]["issues"]
        issues += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return issues
        after = page["pageInfo"]["endCursor"]


def label_time(issue, name):
    """When `name` was last applied, or None."""
    times = [
        parse_time(n["createdAt"])
        for n in issue["timelineItems"]["nodes"]
        if n["__typename"] == "LabeledEvent" and n["label"]["name"] == name
    ]
    return max(times, default=None)


def last_human_comment(issue, after=None, by_author=False):
    """Latest non-bot comment (optionally only by the issue author) newer than `after`."""
    author = issue["author"]["login"] if issue["author"] else None
    times = [
        parse_time(c["createdAt"])
        for c in issue["comments"]["nodes"]
        if not is_bot(c["author"]) and (not by_author or c["author"]["login"] == author)
    ]
    times = [t for t in times if after is None or t > after]
    return max(times, default=None)


def merged_references(issue):
    """Merged PRs that mention the issue without closing it."""
    return [
        (n["source"]["number"], parse_time(n["source"]["mergedAt"]))
        for n in issue["timelineItems"]["nodes"]
        if n["__typename"] == "CrossReferencedEvent"
        and not n["willCloseTarget"]
        and n["source"].get("merged")
    ]


def decide(issue, now, cfg):
    """Returns the actions for one issue: ("label", name, comment), ("unlabel", name) or ("close", comment).
    Pure, so the tests can cover every rule."""
    labels = {n["name"] for n in issue["labels"]["nodes"]}
    managed = {cfg["stale_label"], cfg["resolved_label"]}
    actions = []

    # A human comment after a managed label removes it, even on an exempt issue.
    for name in sorted(labels & managed):
        applied = label_time(issue, name)
        if applied and last_human_comment(issue, after=applied):
            actions.append(("unlabel", name))
            labels.discard(name)

    if labels & set(cfg["exempt_labels"]) or issue["milestone"]:
        return actions

    created = parse_time(issue["createdAt"])
    activity = max(filter(None, [created, last_human_comment(issue)]))

    def days(t):
        return (now - t).days

    # Rule: waiting on the reporter. The only rule that closes.
    waiting = labels & set(cfg["needs_info_labels"])
    if waiting:
        asked = max(label_time(issue, name) or created for name in waiting)
        if not last_human_comment(issue, after=asked, by_author=True):
            stale_at = label_time(issue, cfg["stale_label"]) if cfg["stale_label"] in labels else None
            if stale_at and days(stale_at) >= cfg["close_days"]:
                actions.append(("close", cfg["close_comment"].format(days=cfg["warning_days"] + cfg["close_days"])))
            elif not stale_at and days(asked) >= cfg["warning_days"]:
                actions.append(("label", cfg["stale_label"], cfg["warning_comment"].format(days=cfg["close_days"])))
        return actions

    # Rule: possibly resolved. Never closes.
    if cfg["resolved_label"] not in labels:
        for number, merged in sorted(merged_references(issue), key=lambda r: r[1], reverse=True):
            if days(merged) >= cfg["resolved_grace_days"] and activity < merged:
                actions.append(("label", cfg["resolved_label"], cfg["resolved_comment"].format(pr=number)))
                return actions

    # Rule: idle and untriaged. Never closes. Off unless the repository names its triage label.
    if (
        cfg["triage_label"]
        and cfg["triage_label"] in labels
        and cfg["stale_label"] not in labels
        and not (cfg["priority_prefix"] and any(n.startswith(cfg["priority_prefix"]) for n in labels))
        and days(activity) >= cfg["idle_days"]
    ):
        actions.append(("label", cfg["stale_label"], cfg["idle_comment"].format(days=days(activity))))
    return actions


def ensure_label(repo, name):
    color = LABEL_COLORS.get(name, "EDEDED")
    try:
        gh("label", "create", name, "-R", repo, "--color", color)
    except RuntimeError as err:
        if "already exists" not in str(err):
            raise


def apply(repo, number, action):
    kind = action[0]
    if kind == "label":
        ensure_label(repo, action[1])
        gh("issue", "edit", str(number), "-R", repo, "--add-label", action[1])
        gh("issue", "comment", str(number), "-R", repo, "--body", action[2])
    elif kind == "unlabel":
        gh("issue", "edit", str(number), "-R", repo, "--remove-label", action[1])
    elif kind == "close":
        gh("issue", "comment", str(number), "-R", repo, "--body", action[1])
        gh("issue", "close", str(number), "-R", repo, "--reason", "not planned")


def describe(action):
    return action[0] if action[0] == "close" else f"{action[0]} {action[1]}"


def main():
    dry_run = os.environ.get("DRY_RUN", "true")
    if dry_run not in ("true", "false"):
        log("DRY_RUN must be true or false")
        return 1
    apply_changes = dry_run == "false"
    try:
        max_actions = int(os.environ.get("MAX_ACTIONS", "30"))
        config_path = Path(os.environ.get("ISSUE_CONFIG", ".github/issue-management.json"))
        cfg = load_config(config_path)
        issues = fetch_issues(os.environ["GITHUB_REPOSITORY"])
    except (ValueError, KeyError, RuntimeError) as err:
        log(f"{type(err).__name__}: {err}")
        return 1

    now = datetime.now(timezone.utc)
    rows, changed = [], 0
    for issue in issues:
        actions = decide(issue, now, cfg)
        if not actions:
            continue
        if apply_changes and changed >= max_actions:
            rows.append(f"| #{issue['number']} | skipped: max-actions reached | |")
            continue
        changed += 1
        for action in actions:
            rows.append(f"| #{issue['number']} | {describe(action)} | {issue['title'][:70].replace('|', '/')} |")
            if apply_changes:
                try:
                    apply(os.environ["GITHUB_REPOSITORY"], issue["number"], action)
                except RuntimeError as err:
                    log(str(err))
                    return 1

    print(f"## Issue management ({'applied' if apply_changes else 'dry-run'})\n")
    print(f"Scanned {len(issues)} open issues, {changed} with actions.\n")
    if rows:
        print("| Issue | Action | Title |\n|---|---|---|")
        print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
