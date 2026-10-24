"""GraphQL fetch for pr-queue — stdlib only, one tool call replaces N sequential
pull_request_read calls.

Two real network passes per call: a search query to resolve candidate PR numbers (mirrors
pr-readiness-check.yaml's own lightweight search step), then a detail query per number, run
concurrently via a thread pool (stdlib `concurrent.futures` — no new dependency). Both talk to
GitHub's GraphQL API directly with the profile's own REPO_GITHUB_TOKEN (same scopes already granted
to the github MCP server) — the model sees one tool call regardless of how many real HTTP
requests happen inside it.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

_GRAPHQL_URL = "https://api.github.com/graphql"
_USER_AGENT = "kyverno-assistant-fetch-plugin"

# Mirror of kyverno-sequencer/sequencer.py's _CROSS_REF_PATTERN — kept in sync manually.
# Covers real phrasing variants, not just the canonical form: "Parent: #17701", "Stacks on
# top of #17695", "depends on kyverno/kyverno#17720", "Blocked by #N".
_CROSS_REF_PATTERN = re.compile(
    r"\b(?:depends on|blocked by|requires|stack(?:s|ed)?\s+on(?:\s+top\s+of)?|parent)\b"
    r"\s*:?\s*(?:([\w.-]+/[\w.-]+))?#(\d+)",
    re.IGNORECASE,
)


class FetchError(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("REPO_GITHUB_TOKEN")
    if not token:
        raise FetchError("REPO_GITHUB_TOKEN not set in this profile's environment")
    return token


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        _GRAPHQL_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise FetchError(f"GitHub GraphQL HTTP {exc.code}: {detail}") from exc
    if payload.get("errors"):
        raise FetchError(f"GitHub GraphQL error: {payload['errors']}")
    return payload["data"]


def _rest_get(path: str) -> Any:
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise FetchError(f"GitHub REST HTTP {exc.code}: {detail}") from exc


_SEARCH_QUERY = """
query($search: String!, $first: Int!) {
  search(query: $search, type: ISSUE, first: $first) {
    issueCount
    nodes { ... on PullRequest { number } }
  }
}
"""

# Field shape mirrors .github/workflows/pr-readiness-check.yaml's own detail_query — that
# workflow already proves this shape works at scale against this repo. hasNextPage on each
# >100-item connection is surfaced as a *_truncated flag rather than fully paginated here —
# true for an occasional huge PR, not worth the extra round trips for the common case.
_DETAIL_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      title
      url
      body
      author { login }
      authorAssociation
      createdAt
      baseRefName
      headRefName
      additions
      deletions
      changedFiles
      milestone { title dueOn }
      labels(first: 100) { pageInfo { hasNextPage } nodes { name } }
      files(first: 100) { pageInfo { hasNextPage } nodes { path } }
      reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }
      reviews(last: 50) { nodes { author { login } state submittedAt body } }
      mergeStateStatus
      bumpCommits: commits(first: 10) { nodes { commit { messageBody } } }
      commits(last: 1) { nodes { commit {
        statusCheckRollup {
          contexts(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              __typename
              ... on CheckRun { name conclusion status }
              ... on StatusContext { context state }
            }
          }
        }
      } } }
      closingIssuesReferences(first: 20) {
        nodes { number milestone { title state } labels(first: 20) { nodes { name } } }
      }
    }
  }
}
"""


def _compute_ci_state(contexts: list[dict]) -> str:
    """statusCheckRollup's own single .state field can't be trusted: it blends the 'E2E Gate'
    status context (the branch-wide gate, already surfaced separately via gate_blocked) in
    with the PR's own checks, so a gated PR shows FAILURE here even when every real check is
    green. Exclude that one context and derive the real state from what's left."""
    relevant = [c for c in contexts if c.get("context") != "E2E Gate"]
    if not relevant:
        return "UNKNOWN"
    has_failure = False
    has_pending = False
    for c in relevant:
        if c.get("__typename") == "CheckRun":
            if c.get("status") != "COMPLETED":
                has_pending = True
            elif c.get("conclusion") not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                has_failure = True
        else:  # StatusContext
            state = c.get("state")
            if state == "PENDING":
                has_pending = True
            elif state != "SUCCESS":
                has_failure = True
    if has_failure:
        return "FAILURE"
    if has_pending:
        return "PENDING"
    return "SUCCESS"


_SEMVER_LEVELS = ("major", "minor", "patch")
_COPILOT_LOGIN_PREFIX = "copilot-pull-request-reviewer"
_BUMP_BODY_PATTERNS = (
    re.compile(r"Updates `([^`]+)` from (\S+) to (\S+)"),
    re.compile(r"Bumps \[([^\]]+)\]\([^)]*\) from (\S+) to (\S+)"),
)


def _parse_trailer_bumps(commit_bodies: list[str]) -> list[dict[str, Any]]:
    """Every `updated-dependencies:` entry across all commits (a rebase can move the block off
    the tip). Later commits win on a repeated name."""
    by_name: dict[str, dict[str, Any]] = {}
    for text in commit_bodies:
        in_block = False
        cur: Optional[dict[str, Any]] = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "updated-dependencies:":
                in_block = True
                continue
            if not in_block:
                continue
            if stripped in ("...", "---"):
                in_block = False
                cur = None
                continue
            m = re.match(r"^-\s+([\w-]+):\s*(.*)$", line)
            if m:
                cur = {}
                cur[m.group(1)] = m.group(2).strip()
                if m.group(1) == "dependency-name":
                    by_name[cur["dependency-name"]] = cur
                continue
            m = re.match(r"^\s+([\w-]+):\s*(.*)$", line)
            if m and cur is not None:
                cur[m.group(1)] = m.group(2).strip()
    return [
        {
            "name": name,
            "to": e.get("dependency-version"),
            "update_type": e.get("update-type"),
            "group": e.get("dependency-group"),
        }
        for name, e in by_name.items()
    ]


def _semver_level(bumps: list[dict[str, Any]]) -> str:
    """Same rule as dependabot-merge-triage.yaml: unknown unless every entry has a recognized
    semver level, otherwise the highest across them."""
    if not bumps:
        return "unknown"
    levels = []
    for b in bumps:
        ut = b.get("update_type") or ""
        m = re.fullmatch(r"version-update:semver-(major|minor|patch)", ut)
        if not m:
            return "unknown"
        levels.append(m.group(1))
    return next(l for l in _SEMVER_LEVELS if l in levels)


def _dependabot_bumps(commit_bodies: list[str], pr_body: str) -> list[dict[str, Any]]:
    bumps = _parse_trailer_bumps(commit_bodies)
    froms: dict[str, str] = {}
    for pat in _BUMP_BODY_PATTERNS:
        for name, old, _new in pat.findall(pr_body):
            froms.setdefault(name, old)
    for b in bumps:
        b["from"] = froms.get(b["name"])
    return bumps


def _copilot_review(reviews: list[dict]) -> Optional[dict[str, Any]]:
    """Latest Copilot review, read the way dependabot-merge-triage.yaml reads it: the `###`
    heading in its body is the verdict, and `### 🟢 Approved` is the only approval."""
    mine = [r for r in reviews if (r["author"] or {}).get("login", "").startswith(_COPILOT_LOGIN_PREFIX)]
    if not mine:
        return None
    last = mine[-1]
    body = last.get("body") or ""
    heading = re.search(r"^###\s+(.+)$", body, re.M)
    summary = None
    if heading:
        after = body[heading.end():].strip().splitlines()
        summary = next((l.strip() for l in after if l.strip()), None)
    findings = re.search(r"\*\*Findings:\*\*\s*(\d+)", body)
    return {
        "verdict": re.sub(r"^[^\w]+", "", heading.group(1)).strip() if heading else None,
        "approved": "### 🟢 Approved" in body,
        "summary": summary,
        "findings": int(findings.group(1)) if findings else None,
        "submitted_at": last.get("submittedAt"),
        "reviews_by_copilot": len(mine),
    }


def _fetch_one(owner: str, name: str, number: int) -> dict[str, Any]:
    data = _graphql(_DETAIL_QUERY, {"owner": owner, "name": name, "number": number})
    pr = data["repository"]["pullRequest"]
    if pr is None:
        return {"number": number, "error": "not found (closed or renumbered mid-fetch?)"}

    threads = pr["reviewThreads"]["nodes"]
    reviews = pr["reviews"]["nodes"]
    # CodeRabbit's real GraphQL author.login is "coderabbitai" (no "[bot]" suffix — that's a
    # REST/UI-only convention; GraphQL's Bot actor doesn't carry it).
    coderabbit_approved = any(
        r["author"] and r["author"]["login"] == "coderabbitai" and r["state"] == "APPROVED"
        for r in reviews
    )
    author_login = (pr["author"] or {}).get("login")
    is_dependabot = author_login in ("dependabot", "dependabot[bot]", "app/dependabot")
    bumps = (
        _dependabot_bumps(
            [n["commit"]["messageBody"] or "" for n in pr["bumpCommits"]["nodes"]], pr["body"] or ""
        )
        if is_dependabot
        else []
    )
    commit_nodes = pr["commits"]["nodes"]
    rollup = (commit_nodes[0]["commit"]["statusCheckRollup"] or {}) if commit_nodes else {}
    contexts_conn = rollup.get("contexts") or {"nodes": [], "pageInfo": {"hasNextPage": False}}

    return {
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["url"],
        "body": pr["body"] or "",
        "author": author_login,
        "is_dependabot": is_dependabot,
        "merge_state": pr["mergeStateStatus"],
        "copilot_review": _copilot_review(reviews),
        "dependency_bumps": [b["name"] for b in bumps],
        "bumps": bumps,
        "semver_level": _semver_level(bumps) if is_dependabot else None,
        "author_association": pr["authorAssociation"],
        "created_at": pr["createdAt"],
        "base_branch": pr["baseRefName"],
        "head_branch": pr["headRefName"],
        "size": {"files": pr["changedFiles"], "additions": pr["additions"], "deletions": pr["deletions"]},
        "milestone": (pr["milestone"] or {}).get("title"),
        "milestone_due_on": (pr["milestone"] or {}).get("dueOn"),
        "labels": [n["name"] for n in pr["labels"]["nodes"]],
        "labels_truncated": pr["labels"]["pageInfo"]["hasNextPage"],
        "changed_files": [n["path"] for n in pr["files"]["nodes"]],
        "files_truncated": pr["files"]["pageInfo"]["hasNextPage"],
        "unresolved_review_threads": sum(1 for t in threads if not t["isResolved"]),
        "review_threads_truncated": pr["reviewThreads"]["pageInfo"]["hasNextPage"],
        "coderabbit_approved": coderabbit_approved,
        "ci_state": _compute_ci_state(contexts_conn["nodes"]),
        "ci_state_truncated": contexts_conn["pageInfo"]["hasNextPage"],
        "closing_issues": [
            {
                "number": n["number"],
                "milestone": (n["milestone"] or {}).get("title"),
                "milestone_open": bool(n["milestone"] and n["milestone"]["state"] == "OPEN"),
                # release-critical/-high/-medium/-low live here, on the issue — never on the
                # PR itself, and not applied/removed by any workflow (a maintainer judgment
                # call, not automation) — see kyverno-context's label-taxonomy reference.
                "labels": [l["name"] for l in n["labels"]["nodes"]],
            }
            for n in pr["closingIssuesReferences"]["nodes"]
        ],
    }


# Everything sequence_prs/the severity ladder read, minus the free text that causes spillover
# (body, copilot_review's parsed text) — keeps a whole-candidate-set ranking pass small.
_PR_DIGEST_FIELDS = (
    "number", "title", "url", "author", "is_dependabot", "merge_state", "dependency_bumps",
    "bumps", "semver_level", "author_association", "created_at", "base_branch", "head_branch",
    "size", "milestone", "milestone_due_on", "labels", "labels_truncated", "changed_files",
    "files_truncated", "unresolved_review_threads", "review_threads_truncated",
    "coderabbit_approved", "ci_state", "ci_state_truncated", "closing_issues",
    "external_references", "maintainer_can_modify", "staled_at", "last_commit_at",
    "comment_count", "author_comments_since_stale",
)


def _pr_digest(pr: dict[str, Any]) -> dict[str, Any]:
    if "error" in pr:
        return pr
    digest = {k: pr[k] for k in _PR_DIGEST_FIELDS if k in pr}
    # closing_issues' assignees/other_closing_prs (added by _fetch_one_stale) are themselves
    # small — titles and logins, never a PR/issue body — kept as-is, not stripped further.
    return digest


# A body reference ("Parent: #N") may name an issue, not a PR — GitHub shares one number
# sequence between them. issueOrPullRequest resolves either without erroring on a type
# mismatch the way a plain pullRequest(number:) field does.
_REF_STATE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issueOrPullRequest(number: $number) {
      __typename
      ... on Issue { number title state }
      ... on PullRequest { number title state }
    }
  }
}
"""


def _resolve_ref_state(owner: str, name: str, number: int) -> dict[str, Any]:
    data = _graphql(_REF_STATE_QUERY, {"owner": owner, "name": name, "number": number})
    node = data["repository"]["issueOrPullRequest"]
    if node is None:
        return {"number": number, "kind": "unknown", "state": "NOT_FOUND", "title": None}
    kind = "pr" if node["__typename"] == "PullRequest" else "issue"
    return {"number": node["number"], "kind": kind, "state": node["state"], "title": node["title"]}


# Only run for PRs the caller already knows are stale/author-blocked — these fields cost a
# second GraphQL round trip each, so they stay off the common fetch_pr_candidates path.
_STALE_DETAIL_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      maintainerCanModify
      comments(last: 20) { totalCount nodes { author { login } body createdAt } }
      timelineItems(first: 50, itemTypes: [LABELED_EVENT]) {
        nodes { ... on LabeledEvent { createdAt label { name } } }
      }
      commits(last: 1) { nodes { commit { committedDate } } }
    }
  }
}
"""


def _fetch_one_stale(owner: str, name: str, number: int) -> dict[str, Any]:
    """Enriches _fetch_one's normal PR fetch with the extra facts an author-blocked decision
    needs: when it actually went stale, the author's own comments since, days since their last
    commit, whether the maintainer can push to the branch, and — reusing _fetch_issue_one rather
    than a second closing-issue lookup — each closing issue's real assignees and every other open
    PR also claiming to close it."""
    pr = _fetch_one(owner, name, number)
    if "error" in pr:
        return pr

    data = _graphql(_STALE_DETAIL_QUERY, {"owner": owner, "name": name, "number": number})
    extra = data["repository"]["pullRequest"]

    staled_at = next(
        (
            item["createdAt"]
            for item in extra["timelineItems"]["nodes"]
            if item["label"]["name"] == "stale"
        ),
        None,
    )
    commit_nodes = extra["commits"]["nodes"]
    last_commit_at = commit_nodes[0]["commit"]["committedDate"] if commit_nodes else None

    pr["maintainer_can_modify"] = extra["maintainerCanModify"]
    pr["staled_at"] = staled_at
    pr["last_commit_at"] = last_commit_at
    pr["comment_count"] = extra["comments"]["totalCount"]
    pr["author_comments_since_stale"] = [
        {"body": c["body"], "created_at": c["createdAt"]}
        for c in extra["comments"]["nodes"]
        if (c["author"] or {}).get("login") == pr["author"]
        and (staled_at is None or c["createdAt"] > staled_at)
    ]

    for issue in pr["closing_issues"]:
        try:
            issue_detail = _fetch_issue_one(owner, name, issue["number"])
        except FetchError:
            continue
        issue["assignees"] = issue_detail.get("assignees", [])
        issue["other_closing_prs"] = [
            p for p in issue_detail.get("closing_prs", []) if p["number"] != number
        ]
    return pr


# Safe ceiling for one digest call — changed_files lists alone push ~90 PRs past the 100K
# spillover threshold even with body/review text stripped.
_DIGEST_LIMIT_CEILING = 60


def fetch_pr_candidates(
    repo: str, search_query: str, limit: int = 15, include_stale_detail: bool = False,
    detail: str = "full",
) -> dict[str, Any]:
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)
    if detail == "digest":
        limit = min(limit, _DIGEST_LIMIT_CEILING)

    full_search = f"repo:{repo} is:pr is:open draft:false {search_query}".strip()
    search_data = _graphql(_SEARCH_QUERY, {"search": full_search, "first": min(limit, 100)})
    search_result = search_data["search"]
    numbers = [n["number"] for n in search_result["nodes"] if n]
    total_count = search_result["issueCount"]

    fetch_one = _fetch_one_stale if include_stale_detail else _fetch_one
    prs: list[dict[str, Any]] = []
    errors: list[str] = []
    if numbers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(numbers))) as pool:
            futures = {pool.submit(fetch_one, owner, name, n): n for n in numbers}
            for future in concurrent.futures.as_completed(futures):
                n = futures[future]
                try:
                    prs.append(future.result())
                except FetchError as exc:
                    errors.append(f"PR #{n}: {exc}")

    # A PR's body can reference another PR ("Depends on #N", "Parent: #N", "Stacks on top of
    # #N") that isn't in this fetched set — stale/already-merged, or just outside the slice.
    # Resolve each unique one's real state in this same call instead of leaving it as a
    # "please go check" note.
    fetched_numbers = {p["number"] for p in prs if "number" in p}
    refs_by_pr: dict[int, set[int]] = {}
    current_repo = f"{owner}/{name}".lower()
    for p in prs:
        refs: set[int] = set()
        for match in _CROSS_REF_PATTERN.finditer(p.get("body") or ""):
            repo_part, num_str = match.group(1), match.group(2)
            if repo_part and repo_part.lower() != current_repo:
                continue
            refs.add(int(num_str))
        external = {r for r in refs if r not in fetched_numbers and r != p["number"]}
        if external:
            refs_by_pr[p["number"]] = external

    if refs_by_pr:
        unique_external = {r for refs in refs_by_pr.values() for r in refs}
        resolved: dict[int, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(unique_external))) as pool:
            futures = {pool.submit(_resolve_ref_state, owner, name, n): n for n in unique_external}
            for future in concurrent.futures.as_completed(futures):
                n = futures[future]
                try:
                    resolved[n] = future.result()
                except FetchError as exc:
                    resolved[n] = {"number": n, "state": "UNKNOWN", "title": None, "error": str(exc)}
        for p in prs:
            if p["number"] in refs_by_pr:
                p["external_references"] = [resolved[n] for n in sorted(refs_by_pr[p["number"]])]

    if detail == "digest":
        prs = [_pr_digest(p) for p in prs]

    prs.sort(key=lambda p: p.get("number", 0))
    return {
        "total_count": total_count,
        "fetched": len(prs),
        "truncated": total_count > len(prs),
        "prs": prs,
        "errors": errors,
    }


def fetch_pr_details(
    repo: str, pr_numbers: list[int], include_stale_detail: bool = False
) -> dict[str, Any]:
    """Phase 2 of the digest/full split: full detail (body, diffs, review text) for an explicit
    list of PR numbers already chosen by a digest-ranked pass — skips the search step."""
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)

    fetch_one = _fetch_one_stale if include_stale_detail else _fetch_one
    prs: list[dict[str, Any]] = []
    errors: list[str] = []
    if pr_numbers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(pr_numbers))) as pool:
            futures = {pool.submit(fetch_one, owner, name, n): n for n in pr_numbers}
            for future in concurrent.futures.as_completed(futures):
                n = futures[future]
                try:
                    prs.append(future.result())
                except FetchError as exc:
                    errors.append(f"PR #{n}: {exc}")

    prs.sort(key=lambda p: p.get("number", 0))
    return {"fetched": len(prs), "prs": prs, "errors": errors}


_ISSUE_SEARCH_QUERY = """
query($search: String!, $first: Int!) {
  search(query: $search, type: ISSUE, first: $first) {
    issueCount
    nodes { ... on Issue { number } }
  }
}
"""

# closedByPullRequestsReferences resolves every PR whose body/commits name a closing keyword
# for this issue, open or merged — GitHub's own keyword parser, not a body-text regex, so a
# second competing PR shows up here too. ClosedEvent's `closer` union names the PR or commit
# that actually closed it, when it did.
_ISSUE_DETAIL_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number
      title
      url
      body
      author { login }
      authorAssociation
      createdAt
      state
      stateReason
      milestone { title dueOn state }
      labels(first: 100) { pageInfo { hasNextPage } nodes { name } }
      assignees(first: 20) { pageInfo { hasNextPage } nodes { login } }
      comments(last: 20) { totalCount nodes { author { login } body createdAt } }
      closedByPullRequestsReferences(first: 20, includeClosedPrs: true) {
        pageInfo { hasNextPage }
        nodes { number title state }
      }
      timelineItems(first: 50, itemTypes: [CLOSED_EVENT, REOPENED_EVENT]) {
        pageInfo { hasNextPage }
        nodes {
          __typename
          ... on ClosedEvent {
            createdAt
            closer {
              __typename
              ... on PullRequest { number }
              ... on Commit { oid }
            }
          }
          ... on ReopenedEvent { createdAt }
        }
      }
      parent { number title state }
      subIssues(first: 10) { nodes { number title state } }
      trackedInIssues(first: 10) { nodes { number title state } }
    }
  }
}
"""


def _fetch_issue_one(owner: str, name: str, number: int) -> dict[str, Any]:
    data = _graphql(_ISSUE_DETAIL_QUERY, {"owner": owner, "name": name, "number": number})
    issue = data["repository"]["issue"]
    if issue is None:
        return {"number": number, "error": "not found (deleted, transferred, or renumbered mid-fetch?)"}

    timeline = issue["timelineItems"]["nodes"]
    closed_by: Optional[dict[str, Any]] = None
    for item in timeline:
        if item["__typename"] != "ClosedEvent":
            continue
        closer = item.get("closer") or {}
        if closer.get("__typename") == "PullRequest":
            closed_by = {"pr_number": closer["number"], "commit_oid": None, "closed_at": item["createdAt"]}
        elif closer.get("__typename") == "Commit":
            closed_by = {"pr_number": None, "commit_oid": closer["oid"], "closed_at": item["createdAt"]}
    reopened = any(item["__typename"] == "ReopenedEvent" for item in timeline)

    milestone = issue["milestone"] or {}
    body = issue["body"] or ""
    return {
        "number": issue["number"],
        "title": issue["title"],
        "url": issue["url"],
        "body": body,
        # GitHub's own sub-issue/tracking links — real when present, never a text guess.
        # Rare in practice (most contributors write the relationship as prose instead), so
        # absence here doesn't mean no relationship exists — read the body for that.
        "parent": issue["parent"],
        "sub_issues": [n for n in issue["subIssues"]["nodes"]],
        "tracked_in_issues": [n for n in issue["trackedInIssues"]["nodes"]],
        "author": (issue["author"] or {}).get("login"),
        "author_association": issue["authorAssociation"],
        "created_at": issue["createdAt"],
        "state": issue["state"],
        "state_reason": issue["stateReason"],
        "milestone": milestone.get("title"),
        "milestone_open": milestone.get("state") == "OPEN" if milestone else None,
        "milestone_due_on": milestone.get("dueOn"),
        "labels": [n["name"] for n in issue["labels"]["nodes"]],
        "labels_truncated": issue["labels"]["pageInfo"]["hasNextPage"],
        "assignees": [n["login"] for n in issue["assignees"]["nodes"]],
        "assignees_truncated": issue["assignees"]["pageInfo"]["hasNextPage"],
        "comment_count": issue["comments"]["totalCount"],
        "recent_comments": [
            {
                "author": (c["author"] or {}).get("login"),
                "body": c["body"],
                "created_at": c["createdAt"],
            }
            for c in issue["comments"]["nodes"]
        ],
        # Every PR referencing a close for this issue, not just the one that actually closed
        # it — a second, competing PR against the same issue shows up here too.
        "closing_prs": [
            {"number": n["number"], "title": n["title"], "state": n["state"]}
            for n in issue["closedByPullRequestsReferences"]["nodes"]
        ],
        "closing_prs_truncated": issue["closedByPullRequestsReferences"]["pageInfo"]["hasNextPage"],
        # The PR/commit that actually closed it, when the timeline window covers that event —
        # distinct from closing_prs above, which includes PRs that reference it but never
        # merged. None if still open, or if the close predates this 50-item window.
        "closed_by": closed_by,
        "reopened_since_closed": reopened,
    }


# Same idea as _PR_DIGEST_FIELDS: everything a relational default (no linked PR, milestone
# health, duplicate-effort detection) needs, minus the issue body and comment text.
_ISSUE_DIGEST_FIELDS = (
    "number", "title", "url", "author", "author_association", "created_at", "state",
    "state_reason", "milestone", "milestone_open", "milestone_due_on", "labels",
    "labels_truncated", "assignees", "assignees_truncated", "comment_count", "closing_prs",
    "closing_prs_truncated", "closed_by", "reopened_since_closed", "parent", "sub_issues",
    "tracked_in_issues",
)


def _issue_digest(issue: dict[str, Any]) -> dict[str, Any]:
    if "error" in issue:
        return issue
    return {k: issue[k] for k in _ISSUE_DIGEST_FIELDS if k in issue}


def fetch_issue_candidates(
    repo: str, search_query: str, limit: int = 15, detail: str = "full"
) -> dict[str, Any]:
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)

    full_search = f"repo:{repo} is:issue {search_query}".strip()
    search_data = _graphql(_ISSUE_SEARCH_QUERY, {"search": full_search, "first": min(limit, 100)})
    search_result = search_data["search"]
    numbers = [n["number"] for n in search_result["nodes"] if n]
    total_count = search_result["issueCount"]

    issues: list[dict[str, Any]] = []
    errors: list[str] = []
    if numbers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(numbers))) as pool:
            futures = {pool.submit(_fetch_issue_one, owner, name, n): n for n in numbers}
            for future in concurrent.futures.as_completed(futures):
                n = futures[future]
                try:
                    issues.append(future.result())
                except FetchError as exc:
                    errors.append(f"Issue #{n}: {exc}")

    if detail == "digest":
        issues = [_issue_digest(i) for i in issues]

    issues.sort(key=lambda i: i.get("number", 0))
    return {
        "total_count": total_count,
        "fetched": len(issues),
        "truncated": total_count > len(issues),
        "issues": issues,
        "errors": errors,
    }


def fetch_issue_details(repo: str, issue_numbers: list[int]) -> dict[str, Any]:
    """Phase 2 of the digest/full split: full detail (body, comments) for an explicit list of
    issue numbers already chosen by a digest-ranked pass — skips the search step entirely."""
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)

    issues: list[dict[str, Any]] = []
    errors: list[str] = []
    if issue_numbers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(issue_numbers))) as pool:
            futures = {pool.submit(_fetch_issue_one, owner, name, n): n for n in issue_numbers}
            for future in concurrent.futures.as_completed(futures):
                n = futures[future]
                try:
                    issues.append(future.result())
                except FetchError as exc:
                    errors.append(f"Issue #{n}: {exc}")

    issues.sort(key=lambda i: i.get("number", 0))
    return {"fetched": len(issues), "issues": issues, "errors": errors}


def _get_patch_for_file(owner: str, name: str, number: int, path: str) -> Optional[str]:
    page = 1
    while page <= 5:  # 500 files; a PR touching more than that needs a human regardless
        files = _rest_get(f"repos/{owner}/{name}/pulls/{number}/files?per_page=100&page={page}")
        for f in files:
            if f.get("filename") == path:
                return f.get("patch")  # absent for binary/very large files
        if len(files) < 100:
            return None
        page += 1
    return None


def fetch_file_diff_overlap(repo: str, path: str, pr_numbers: list[int]) -> dict[str, Any]:
    """Real diff hunks each listed PR has for one specific file (REST — GraphQL has no patch
    field). Deliberately returns raw patches only, no computed overlap verdict: each PR's
    hunk line numbers are expressed against its own merge-base with main, which can differ
    between PRs, so comparing line ranges across PRs can't be made reliable — reading what
    each patch actually does is the real check, not a number."""
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)

    by_pr: dict[int, Optional[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(pr_numbers))) as pool:
        futures = {pool.submit(_get_patch_for_file, owner, name, n, path): n for n in pr_numbers}
        for future in concurrent.futures.as_completed(futures):
            by_pr[futures[future]] = future.result()

    return {"path": path, "by_pr": by_pr}
