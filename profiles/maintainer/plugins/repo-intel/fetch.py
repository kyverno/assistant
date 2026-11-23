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
import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .pack import PackError, Rules, load as load_pack
from .sequencer import CROSS_REF_PATTERN, gate_blocked, pr_from_dict

_GRAPHQL_URL = "https://api.github.com/graphql"
_USER_AGENT = "repo-intel-fetch-plugin"
# GitHub asks for serial requests to stay clear of its secondary rate limits; two at once keeps a 15-PR fetch quick.
_MAX_WORKERS = 2
# Above this many characters a result is trimmed (bodies first, then trailing items) instead of spilling to a file.
RESULT_CHAR_CAP = 40_000

class FetchError(RuntimeError):
    pass


class NotFound(FetchError):
    pass


# Full PR records from the last fetch, per repository, so sequence_prs can take PR numbers instead of the model
# re-typing every record; and the repository's gate and default branch from that same fetch.
_PRS: dict[str, dict[int, dict[str, Any]]] = {}
_PR_FETCHED_AT: dict[tuple[str, int], float] = {}
# A cached PR is reused when GitHub's updatedAt for it has not moved and it is younger than this. CI results do not move
# updatedAt, so the age limit keeps check states from going stale.
_PR_REUSE_TTL = 600
_ISSUES: dict[str, dict[int, dict[str, Any]]] = {}
_ISSUE_FETCHED_AT: dict[tuple[str, int], float] = {}
# Things that never change for a given commit: patches by head commit, CODEOWNERS and docs by default-branch commit.
_BY_COMMIT: dict[tuple, Any] = {}
_REPO_STATE: dict[str, tuple[float, dict[str, Any]]] = {}
_REPO_STATE_TTL = 120
_PACK_SHOWN: set[str] = set()


def cached_prs(repo: str, numbers: list[int]) -> tuple[list[dict[str, Any]], list[int]]:
    """The cached full records for `numbers`, and the numbers not in the cache."""
    have = _PRS.get(repo.lower(), {})
    return [have[n] for n in numbers if n in have], [n for n in numbers if n not in have]


def _remember(repo: str, prs: list[dict[str, Any]]) -> None:
    store = _PRS.setdefault(repo.lower(), {})
    for p in prs:
        if "error" not in p:
            if p["number"] not in store or store[p["number"]] is not p:
                _PR_FETCHED_AT[(repo.lower(), p["number"])] = time.time()
            store[p["number"]] = p


def cached_pr(repo: str, number: int, updated_at: Optional[str] = None) -> Optional[dict[str, Any]]:
    """A cached record still fresh enough to reuse: younger than _PR_REUSE_TTL and, when updated_at is given, not
    updated on GitHub since."""
    rec = _PRS.get(repo.lower(), {}).get(number)
    if rec is None or time.time() - _PR_FETCHED_AT.get((repo.lower(), number), 0) > _PR_REUSE_TTL:
        return None
    if updated_at and rec.get("updated_at") and rec["updated_at"] != updated_at:
        return None
    return rec


def cached_issue(repo: str, number: int, updated_at: Optional[str] = None) -> Optional[dict[str, Any]]:
    rec = _ISSUES.get(repo.lower(), {}).get(number)
    if rec is None or time.time() - _ISSUE_FETCHED_AT.get((repo.lower(), number), 0) > _PR_REUSE_TTL:
        return None
    if updated_at and rec.get("updated_at") and rec["updated_at"] != updated_at:
        return None
    return rec


def _remember_issues(repo: str, issues: list[dict[str, Any]]) -> None:
    store = _ISSUES.setdefault(repo.lower(), {})
    for i in issues:
        if "error" not in i:
            if store.get(i["number"]) is not i:
                _ISSUE_FETCHED_AT[(repo.lower(), i["number"])] = time.time()
            store[i["number"]] = i


# Fields only the engine reads (queue and item); the fetch tools leave them out of what the model sees.
_ENGINE_FIELDS = ("files", "head_oid", "updated_at", "review_requests")


def _public(pr: dict[str, Any]) -> dict[str, Any]:
    if "error" in pr:
        return pr
    out = {k: v for k, v in pr.items() if k not in _ENGINE_FIELDS}
    if out.get("closing_issues"):
        out["closing_issues"] = [{k: v for k, v in i.items() if k not in ("body_excerpt", "title", "created_at")}
                                 for i in out["closing_issues"]]
    return out


def _ssl_context() -> ssl.SSLContext:
    # The system store first: in the sandbox it holds the OpenShell proxy's certificate. certifi covers Pythons with
    # no system store (python.org builds on macOS).
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore[import-not-found]

        ctx.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    return ctx


def _rate_limit_error(exc: urllib.error.HTTPError, detail: str, what: str) -> Optional[FetchError]:
    h = {k.lower(): v for k, v in (exc.headers or {}).items()}
    if exc.code in (403, 429) and ("rate limit" in detail.lower() or h.get("x-ratelimit-remaining") == "0"):
        wait = h.get("retry-after")
        if not wait and h.get("x-ratelimit-reset", "").isdigit():
            wait = str(max(1, int(h["x-ratelimit-reset"]) - int(time.time())))
        return FetchError(f"GitHub rate limit hit on {what}; try again in about {wait or 'a minute or two'} seconds. "
                          "Do not retry in a loop.")
    return None


def _open(req: urllib.request.Request, what: str) -> Any:
    """JSON from one request, retried once when the connection breaks or GitHub answers 502/503/504."""
    last: Optional[Exception] = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in (502, 503, 504) and attempt == 0:
                last = exc
                continue
            if exc.code == 404:
                raise NotFound(f"GitHub {what} HTTP 404: {detail}") from exc
            raise _rate_limit_error(exc, detail, what) or FetchError(f"GitHub {what} HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last = exc
    raise FetchError(f"could not reach GitHub for {what}: {last}")


def _rules(repo: Optional[str] = None) -> Rules:
    try:
        return load_pack(repo=repo)
    except PackError as exc:
        raise FetchError(f"organisation pack: {exc}") from exc


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
    payload = _open(req, "GraphQL")
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
    return _open(req, "REST")


_SEARCH_QUERY = """
query($search: String!, $first: Int!) {
  search(query: $search, type: ISSUE, first: $first) {
    issueCount
    nodes { ... on PullRequest { number updatedAt } }
  }
}
"""

# Field shape mirrors .github/workflows/pr-readiness-check.yaml's own detail_query — that
# workflow already proves this shape works at scale against this repo. hasNextPage on each
# >100-item connection is surfaced as a *_truncated flag rather than fully paginated here —
# true for an occasional huge PR, not worth the extra round trips for the common case.
_PR_FIELDS = """
      number
      title
      url
      body
      author { login }
      authorAssociation
      createdAt
      state
      mergedAt
      closedAt
      updatedAt
      baseRefName
      headRefName
      headRefOid
      reviewDecision
      reviewRequests(first: 100) { pageInfo { hasNextPage } nodes { requestedReviewer { __typename ... on User { login } ... on Team { slug } } } }
      additions
      deletions
      changedFiles
      milestone { title dueOn }
      labels(first: 100) { pageInfo { hasNextPage } nodes { name } }
      files(first: 100) { pageInfo { hasNextPage } nodes { path additions deletions changeType } }
      reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }
      reviews(last: 100) { nodes { author { login } state submittedAt body } }
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
        pageInfo { hasNextPage }
        nodes {
          number state title body createdAt reactions { totalCount }
          milestone { title state dueOn } labels(first: 20) { nodes { name } }
          closedByPullRequestsReferences(first: 25, includeClosedPrs: true) {
            pageInfo { hasNextPage } nodes { number title state }
          }
        }
      }
"""
_DETAIL_QUERY = ("query($owner: String!, $name: String!, $number: Int!) {\n"
                 "  repository(owner: $owner, name: $name) {\n    pullRequest(number: $number) {"
                 + _PR_FIELDS + "    }\n  }\n}\n")
# PRs or issues asked for in one query, and batch queries in flight. One query per PR, two at a time, took about 55 s
# for 60 Kyverno PRs; ten to a query and four queries at a time, about 13 s. GitHub answers a batch no faster than its
# PRs one after another, so the gain beyond batching comes from running a few at once.
_DETAIL_BATCH = 10
_BATCH_WORKERS = 4


def _batch_query(field: str, fields: str, count: int) -> str:
    args = ", ".join(f"$n{i}: Int!" for i in range(count))
    items = "".join(f"    p{i}: {field}(number: $n{i}) {{{fields}    }}\n" for i in range(count))
    return f"query($owner: String!, $name: String!, {args}) {{\n  repository(owner: $owner, name: $name) {{\n{items}  }}\n}}\n"


def _fetch_batched(field: str, fields: str, build, one, owner: str, name: str,
                   numbers: list[int]) -> tuple[list[dict[str, Any]], list[str]]:
    """Records for `numbers`, _DETAIL_BATCH to a query. build(node, number) makes a record from a node; one(owner, name,
    number) fetches a single item. If GitHub rejects a batch (one item it cannot read fails the whole query), that
    batch's items are fetched one by one so the others still come back; a rate limit is not retried."""
    def batch(nums: list[int]) -> tuple[list[dict[str, Any]], list[str]]:
        variables: dict[str, Any] = {"owner": owner, "name": name, **{f"n{i}": n for i, n in enumerate(nums)}}
        try:
            repo = _graphql(_batch_query(field, fields, len(nums)), variables)["repository"]
        except FetchError as exc:
            if "rate limit" in str(exc):
                return [], [f"#{n}: {exc}" for n in nums]
            out, errs = [], []
            for n in nums:
                try:
                    out.append(one(owner, name, n))
                except FetchError as single:
                    errs.append(f"#{n}: {single}")
            return out, errs
        return [build(repo.get(f"p{i}"), n) for i, n in enumerate(nums)], []

    # At most _DETAIL_BATCH to a query. Past one round, a multiple of _BATCH_WORKERS batches of even size, so no round
    # runs half empty: 60 PRs go as 8 batches of 7 or 8, not 6 of 10 with the last round two queries wide.
    count = -(-len(numbers) // _DETAIL_BATCH)
    if count > _BATCH_WORKERS:
        count = -(-count // _BATCH_WORKERS) * _BATCH_WORKERS
    batches = [numbers[i::count] for i in range(count)] if numbers else []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if batches:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(_BATCH_WORKERS, len(batches))) as pool:
            for got, errs in pool.map(batch, batches):
                records += got
                errors += errs
    return records, errors


def _gate_checks(contexts: list[dict], rules: Rules) -> dict[str, str]:
    """Each pack gate's own status on this PR's head commit: SUCCESS, FAILURE, PENDING, or MISSING when the gate's
    status never reported. A gate with no status_context is left out."""
    out: dict[str, str] = {}
    for g in rules.gates:
        if not g.status_context:
            continue
        state = "MISSING"
        for c in contexts:
            if c.get("context") == g.status_context:
                state = {"SUCCESS": "SUCCESS", "PENDING": "PENDING", "EXPECTED": "PENDING"}.get(c.get("state"), "FAILURE")
            elif c.get("__typename") == "CheckRun" and c.get("name") == g.status_context:
                if c.get("status") != "COMPLETED":
                    state = "PENDING"
                else:
                    state = "SUCCESS" if c.get("conclusion") in ("SUCCESS", "NEUTRAL", "SKIPPED") else "FAILURE"
        out[g.name] = state
    return out


def _failing_checks(contexts: list[dict], gate_contexts: frozenset) -> list[str]:
    out = []
    for c in contexts:
        name = c.get("name") or c.get("context")
        if not name or name in gate_contexts:
            continue
        if c.get("__typename") == "CheckRun":
            bad = c.get("status") == "COMPLETED" and c.get("conclusion") not in ("SUCCESS", "NEUTRAL", "SKIPPED")
        else:
            bad = c.get("state") in ("FAILURE", "ERROR")
        if bad and name not in out:
            out.append(name)
    return out[:5]


def _compute_ci_state(contexts: list[dict], gate_contexts: frozenset = frozenset()) -> str:
    """statusCheckRollup's own single .state field can't be trusted: it blends a branch gate's
    status context (already surfaced separately via gate_blocked) in with the PR's own checks,
    so a gated PR shows FAILURE here even when every real check is green. Exclude the pack's
    gate contexts and derive the real state from what's left."""
    # A gate can report as a StatusContext (context) or a CheckRun (name); match both, as _failing_checks does.
    relevant = [c for c in contexts if (c.get("name") or c.get("context")) not in gate_contexts]
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


def _fetch_one(owner: str, name: str, number: int, rules: Rules) -> dict[str, Any]:
    data = _graphql(_DETAIL_QUERY, {"owner": owner, "name": name, "number": number})
    return _pr_record(data["repository"]["pullRequest"], number, rules)


def _fetch_details(owner: str, name: str, numbers: list[int], rules: Rules) -> tuple[list[dict[str, Any]], list[str]]:
    return _fetch_batched("pullRequest", _PR_FIELDS, lambda node, n: _pr_record(node, n, rules),
                          lambda o, nm, n: _fetch_one(o, nm, n, rules), owner, name, numbers)


def _pr_record(pr: Optional[dict[str, Any]], number: int, rules: Rules) -> dict[str, Any]:
    if pr is None:
        return {"number": number, "error": "not found (closed or renumbered mid-fetch?)"}

    threads = pr["reviewThreads"]["nodes"]
    reviews = pr["reviews"]["nodes"]
    # GraphQL's Bot actor login has no "[bot]" suffix (that's a REST/UI-only convention), so the
    # pack's approval_bots are written the bare way, e.g. "coderabbitai".
    bot_approved = any(
        r["author"] and r["author"]["login"] in rules.approval_bots and r["state"] == "APPROVED"
        for r in reviews
    )
    # Each reviewer's latest verdict counts, in submission order (reviews(last: 100) comes oldest first).
    verdicts = {
        r["author"]["login"]: r["state"] for r in reviews
        if r["author"] and r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
    }
    human = {k: v for k, v in verdicts.items() if k not in rules.approval_bots}
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
        "state": pr["state"],
        "merged_at": pr["mergedAt"],
        "closed_at": pr["closedAt"],
        "human_approvals": sum(v == "APPROVED" for v in human.values()),
        "changes_requested": any(v == "CHANGES_REQUESTED" for v in human.values()),
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
        "updated_at": pr.get("updatedAt"),
        "head_oid": pr.get("headRefOid"),
        "review_decision": pr.get("reviewDecision"),
        "review_requests": [
            r["requestedReviewer"].get("login") or "team:" + (r["requestedReviewer"].get("slug") or "")
            for r in (pr.get("reviewRequests") or {}).get("nodes", []) if r.get("requestedReviewer")
        ],
        "review_requests_truncated": ((pr.get("reviewRequests") or {}).get("pageInfo") or {}).get("hasNextPage", False),
        "files": [{"path": n["path"], "additions": n.get("additions") or 0, "deletions": n.get("deletions") or 0,
                   "change": n.get("changeType") or "MODIFIED"} for n in pr["files"]["nodes"]],
        "changed_files": [n["path"] for n in pr["files"]["nodes"]],
        "files_truncated": pr["files"]["pageInfo"]["hasNextPage"],
        "unresolved_review_threads": sum(1 for t in threads if not t["isResolved"]),
        "review_threads_truncated": pr["reviewThreads"]["pageInfo"]["hasNextPage"],
        "bot_approved": bot_approved,
        "ci_state": _compute_ci_state(contexts_conn["nodes"], frozenset(g.status_context for g in rules.gates)),
        "ci_state_truncated": contexts_conn["pageInfo"]["hasNextPage"],
        "failing_checks": _failing_checks(contexts_conn["nodes"], frozenset(g.status_context for g in rules.gates)),
        **({"gate_checks": _gate_checks(contexts_conn["nodes"], rules)} if rules.gates else {}),
        "closing_issues": [
            {
                "number": n["number"],
                "state": n["state"],  # CLOSED means another change already fixed it
                "title": n.get("title"),
                "created_at": n.get("createdAt"),
                # The start of the issue's text, for the severity wording the engine looks for; never shown whole.
                "body_excerpt": (n.get("body") or "")[:600],
                "reactions": (n.get("reactions") or {}).get("totalCount", 0),
                "milestone": (n["milestone"] or {}).get("title"),
                "milestone_open": bool(n["milestone"] and n["milestone"]["state"] == "OPEN"),
                "milestone_due_on": (n["milestone"] or {}).get("dueOn"),
                # release-critical/-high/-medium/-low live here, on the issue — never on the
                # PR itself, and not applied/removed by any workflow (a maintainer judgment
                # call, not automation) — see repo-context's label-taxonomy reference.
                "labels": [l["name"] for l in n["labels"]["nodes"]],
                # Every other PR that says it closes this issue: OPEN ones compete with this PR, a MERGED one
                # already landed a fix this PR may duplicate.
                "other_prs": [
                    {"number": o["number"], "title": o["title"], "state": o["state"]}
                    for o in (n.get("closedByPullRequestsReferences") or {}).get("nodes", [])
                    if o["number"] != pr["number"]
                ],
                **({"other_prs_truncated": True}
                   if ((n.get("closedByPullRequestsReferences") or {}).get("pageInfo") or {}).get("hasNextPage") else {}),
            }
            for n in pr["closingIssuesReferences"]["nodes"]
        ],
        "closing_issues_truncated": bool((pr["closingIssuesReferences"].get("pageInfo") or {}).get("hasNextPage")),
    }


# Everything sequence_prs/the severity ladder read, minus the free text that causes spillover
# (body, copilot_review's parsed text) — keeps a whole-candidate-set ranking pass small.
_PR_DIGEST_FIELDS = (
    "number", "title", "url", "author", "is_dependabot", "merge_state", "state", "merged_at", "closed_at",
    "human_approvals", "changes_requested", "dependency_bumps",
    "bumps", "semver_level", "author_association", "created_at", "base_branch", "head_branch",
    "size", "milestone", "milestone_due_on", "labels", "labels_truncated", "changed_files",
    "files_truncated", "unresolved_review_threads", "review_threads_truncated",
    "bot_approved", "ci_state", "ci_state_truncated", "failing_checks", "review_decision", "gate_checks", "gate_blocked",
    "closing_issues", "closing_issues_truncated",
    "external_references", "maintainer_can_modify", "staled_at", "last_commit_at",
    "comment_count", "author_comments_since_stale",
)
# A digest lists a PR's files only up to this many; past it, the directories it touches. sequence_prs reads the full
# list from the cache, so the model never needs it.
_DIGEST_FILE_LIST_MAX = 10


def _pr_digest(pr: dict[str, Any]) -> dict[str, Any]:
    if "error" in pr:
        return pr
    digest = {k: v for k, v in _public(pr).items() if k in _PR_DIGEST_FIELDS}
    files = digest.get("changed_files") or []
    if len(files) > _DIGEST_FILE_LIST_MAX:
        digest["changed_dirs"] = sorted({os.path.dirname(f) or "." for f in files})
        del digest["changed_files"]
    # closing_issues' assignees/other_closing_prs (added by _fetch_one_stale) are themselves
    # small — titles and logins, never a PR/issue body — kept as-is, not stripped further.
    return digest


# Lists outside the items (the gate's failure issues, errors) are cut to this many when they crowd the items out.
_SIDE_LIST_MAX = 10


def cut_list(holder: Optional[dict[str, Any]], key: str, keep: int) -> bool:
    """Keep the first `keep` entries of holder[key] and add the number removed to holder[key + "_not_shown"], so the
    count stays right however many times a list is cut. Returns whether it cut anything."""
    items = (holder or {}).get(key)
    if not isinstance(items, list) or len(items) <= keep:
        return False
    holder[key] = items[:keep]
    holder[f"{key}_not_shown"] = holder.get(f"{key}_not_shown", 0) + len(items) - keep
    return True


def _shrink_sides(result: dict[str, Any], keep: int) -> bool:
    """Cut the gate's failure-issue list and the errors to `keep` entries each, and replace the pack brief with a
    pointer. The gate's blocking_branches stay whole, so gate_blocked is still right. Returns whether it cut anything."""
    if result.get("gate"):
        result["gate"] = dict(result["gate"])  # the repository state is shared; never cut it in place
    cut = cut_list(result.get("gate"), "open_failure_issues", keep)
    cut = cut_list(result, "errors", keep) or cut
    if isinstance(result.get("pack"), dict):
        result["pack"] = "left out to fit; the next call shows it"
        cut = True
    return cut


def fit(result: dict[str, Any], key: str, digest) -> dict[str, Any]:
    """Keep a result under RESULT_CHAR_CAP: first drop free text (the digest form), then cut the lists outside the
    items if they take more than a quarter of the cap, then drop trailing items, naming what was left out so it can be
    fetched by number. If the rest alone is still over the cap, its lists are cut further."""
    if len(json.dumps(result)) <= RESULT_CHAR_CAP:
        return result
    items = [digest(i) for i in result[key]]
    result = {**result, key: items, "trimmed": "long text dropped to fit; fetch the full detail by number"}
    if len(json.dumps({k: v for k, v in result.items() if k != key})) > RESULT_CHAR_CAP // 4:
        _shrink_sides(result, _SIDE_LIST_MAX)
    left_out: list[int] = []
    while items and len(json.dumps(result)) > RESULT_CHAR_CAP:
        left_out.insert(0, items.pop().get("number"))
        result[key] = items
        result["not_shown"] = left_out
    keep = _SIDE_LIST_MAX
    while len(json.dumps(result)) > RESULT_CHAR_CAP and keep > 0:
        keep //= 2
        _shrink_sides(result, keep)
    return result


_REPO_STATE_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { defaultBranchRef { name target { oid } } }
}
"""

_REPO_STATE_GATE_QUERY = """
query($owner: String!, $name: String!, $label: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name target { oid } }
    issues(first: 100, after: $after, states: OPEN, labels: [$label]) {
      pageInfo { hasNextPage endCursor } nodes { number title url body }
    }
  }
}
"""
# Pages of open failure issues read before giving up. Past that the gate fails closed: every branch counts as blocked.
_GATE_PAGES_MAX = 5


def repo_state(owner: str, name: str, rules: Rules) -> dict[str, Any]:
    """The default branch and, when the pack has a gate, its open failure issues and the branches they block. Read
    from the repository's issue list, not search, so an open failure issue is never missed. Without a gate, remembered
    briefly."""
    key = f"{owner}/{name}".lower()
    g = rules.gate
    hit = _REPO_STATE.get(key)
    # Only the default branch is reused: a gate can open or close between two calls, so it is read every time.
    if g is None and hit and time.time() - hit[0] < _REPO_STATE_TTL:
        return hit[1]
    if g is None:
        data = _graphql(_REPO_STATE_QUERY, {"owner": owner, "name": name})["repository"]
        state: dict[str, Any] = {"default_branch": (data["defaultBranchRef"] or {}).get("name") or "main", "gate": None}
    else:
        issues, after, truncated = [], None, False
        for page in range(_GATE_PAGES_MAX):
            data = _graphql(_REPO_STATE_GATE_QUERY, {"owner": owner, "name": name, "label": g.failure_issue_label,
                                                     "after": after})["repository"]
            for i in data["issues"]["nodes"]:
                m = g.branch_marker.search(i["body"] or "") if g.branch_marker else None
                # No marker in the pack, or none in this issue: the issue blocks every branch.
                issues.append({"number": i["number"], "title": i["title"], "url": i["url"],
                               "branch": m.group("branch") if m else None})
            info = data["issues"]["pageInfo"]
            if not info["hasNextPage"]:
                break
            after = info["endCursor"]
            truncated = page == _GATE_PAGES_MAX - 1
        state = {
            "default_branch": (data["defaultBranchRef"] or {}).get("name") or "main",
            "gate": {
                "name": g.name, "failure_issue_label": g.failure_issue_label, "bypass_label": g.bypass_label,
                "open_failure_issues": issues,
                "open_failure_issues_truncated": truncated,
                "blocking_branches": sorted({i["branch"] for i in issues if i["branch"]}),
                # Issues left unread could block any branch, so a truncated list blocks them all.
                "blocks_all_branches": truncated or any(i["branch"] is None for i in issues),
            },
        }
    state["default_oid"] = ((data["defaultBranchRef"] or {}).get("target") or {}).get("oid")
    _REPO_STATE[key] = (time.time(), state)
    return state


def _mark_gate(prs: list[dict[str, Any]], state: dict[str, Any], rules: Rules) -> None:
    gate = state.get("gate")
    if not gate:
        return
    branches = set(gate["blocking_branches"])
    for p in prs:
        if "error" not in p:
            p["gate_blocked"] = gate_blocked(pr_from_dict(p), branches, gate["blocks_all_branches"], rules)


def _pack_brief(rules: Rules) -> dict[str, Any]:
    """The few pack facts every answer needs, sent once per session with the first fetch."""
    s = rules.summary()
    return {
        "source": s["pack"],
        "branch_gates": [g["name"] for g in s["branch_gates"]],
        "ready_labels": s["readiness"]["ready_labels"],
        "approval_bots": s["approval_bots"],
        "review_bots": s["review_bots"],
        "more": "repo_pack has the file classes, labels and the organisation's own facts",
    }


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


def _fetch_one_stale(owner: str, name: str, number: int, rules: Rules) -> dict[str, Any]:
    """Enriches _fetch_one's normal PR fetch with the extra facts an author-blocked decision
    needs: when it actually went stale, the author's own comments since, days since their last
    commit, whether the maintainer can push to the branch, and — reusing _fetch_issue_one rather
    than a second closing-issue lookup — each closing issue's real assignees and every other open
    PR also claiming to close it."""
    pr = _fetch_one(owner, name, number, rules)
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


def _fetch_many(fn, owner: str, name: str, numbers: list[int], *extra) -> tuple[list[dict[str, Any]], list[str]]:
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    if numbers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(numbers))) as pool:
            futures = {pool.submit(fn, owner, name, n, *extra): n for n in numbers}
            for future in concurrent.futures.as_completed(futures):
                try:
                    out.append(future.result())
                except FetchError as exc:
                    errors.append(f"#{futures[future]}: {exc}")
    return out, errors


def collect_prs(repo: str, search_query: str, limit: int = 15,
                include_stale_detail: bool = False) -> tuple[list[dict[str, Any]], int, list[str], dict[str, Any], Rules]:
    """Full records for the open, non-draft PRs a search finds, gate marked and cached: (records, total_count, errors,
    repo_state, rules). A cached record whose updatedAt has not moved is reused instead of fetched again."""
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)

    full_search = f"repo:{repo} is:pr is:open draft:false {search_query}".strip()
    search_result = _graphql(_SEARCH_QUERY, {"search": full_search, "first": min(limit, 100)})["search"]
    hits = [n for n in search_result["nodes"] if n]
    total_count = search_result["issueCount"]

    rules = _rules(repo)
    prs: list[dict[str, Any]] = []
    to_fetch: list[int] = []
    for h in hits:
        rec = None if include_stale_detail else cached_pr(repo, h["number"], h.get("updatedAt"))
        if rec is not None:
            prs.append(rec)
        else:
            to_fetch.append(h["number"])
    if include_stale_detail:
        fetched, errors = _fetch_many(_fetch_one_stale, owner, name, to_fetch, rules)
    else:
        fetched, errors = _fetch_details(owner, name, to_fetch, rules)
    prs += fetched
    errors = [f"PR {e}" for e in errors]

    # A PR's body can reference another PR ("Depends on #N", "Parent: #N", "Stacks on top of
    # #N") that isn't in this fetched set — stale/already-merged, or just outside the slice.
    # Resolve each unique one's real state in this same call instead of leaving it as a
    # "please go check" note.
    fetched_numbers = {p["number"] for p in prs if "number" in p}
    refs_by_pr: dict[int, set[int]] = {}
    current_repo = f"{owner}/{name}".lower()
    for p in prs:
        if "error" in p or "external_references" in p:
            continue
        refs: set[int] = set()
        for match in CROSS_REF_PATTERN.finditer(p.get("body") or ""):
            repo_part, num_str = match.group(1), match.group(2)
            if repo_part and repo_part.lower() != current_repo:
                continue
            refs.add(int(num_str))
        external = {r for r in refs if r not in fetched_numbers and r != p["number"]}
        if external:
            refs_by_pr[p["number"]] = external

    if refs_by_pr:
        unique_external = sorted({r for refs in refs_by_pr.values() for r in refs})
        resolved: dict[int, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(unique_external))) as pool:
            futures = {pool.submit(_resolve_ref_state, owner, name, n): n for n in unique_external}
            for future in concurrent.futures.as_completed(futures):
                n = futures[future]
                try:
                    resolved[n] = future.result()
                except FetchError as exc:
                    resolved[n] = {"number": n, "state": "UNKNOWN", "title": None, "error": str(exc)}
        for p in prs:
            if p.get("number") in refs_by_pr:
                p["external_references"] = [resolved[n] for n in sorted(refs_by_pr[p["number"]])]

    state = repo_state(owner, name, rules)
    _mark_gate(prs, state, rules)
    _remember(repo, prs)
    prs.sort(key=lambda p: p.get("number", 0))
    return prs, total_count, errors, state, rules


def fetch_pr_candidates(
    repo: str, search_query: str, limit: int = 15, include_stale_detail: bool = False,
    detail: str = "full",
) -> dict[str, Any]:
    if detail == "digest":
        limit = min(limit, _DIGEST_LIMIT_CEILING)
    prs, total_count, errors, state, rules = collect_prs(repo, search_query, limit, include_stale_detail)
    shown = [_pr_digest(p) if detail == "digest" else _public(p) for p in prs]
    result: dict[str, Any] = {
        "total_count": total_count,
        "fetched": len(shown),
        "truncated": total_count > len(shown),
        "default_branch": state["default_branch"],
        **({"gate": state["gate"]} if state["gate"] else {}),
        "prs": shown,
        "errors": errors,
    }
    if repo.lower() not in _PACK_SHOWN:
        result["pack"] = _pack_brief(rules)
    result = fit(result, "prs", _pr_digest)
    # Marked shown only once the brief itself went out, not the "left out to fit" pointer.
    if isinstance(result.get("pack"), dict):
        _PACK_SHOWN.add(repo.lower())
    return result


def pr_records(repo: str, pr_numbers: list[int], include_stale_detail: bool = False,
               reuse: bool = False) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], Rules]:
    """Full records for explicit PR numbers, gate marked and cached. reuse=True takes a fresh cached record instead."""
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)
    rules = _rules(repo)
    prs: list[dict[str, Any]] = []
    to_fetch = []
    for n in pr_numbers:
        rec = cached_pr(repo, n) if reuse and not include_stale_detail else None
        if rec is not None:
            prs.append(rec)
        else:
            to_fetch.append(n)
    if include_stale_detail:
        fetched, errors = _fetch_many(_fetch_one_stale, owner, name, to_fetch, rules)
    else:
        fetched, errors = _fetch_details(owner, name, to_fetch, rules)
    prs += fetched
    state = repo_state(owner, name, rules)
    _mark_gate(prs, state, rules)
    _remember(repo, prs)
    prs.sort(key=lambda p: p.get("number", 0))
    return prs, [f"PR {e}" for e in errors], state, rules


def fetch_pr_details(
    repo: str, pr_numbers: list[int], include_stale_detail: bool = False
) -> dict[str, Any]:
    """Phase 2 of the digest/full split: full detail (body, diffs, review text) for an explicit
    list of PR numbers already chosen by a digest-ranked pass — skips the search step."""
    prs, errors, _state, _rules_ = pr_records(repo, pr_numbers, include_stale_detail)
    return fit({"fetched": len(prs), "prs": [_public(p) for p in prs], "errors": errors}, "prs", _pr_digest)


_ISSUE_SEARCH_QUERY = """
query($search: String!, $first: Int!) {
  search(query: $search, type: ISSUE, first: $first) {
    issueCount
    nodes { ... on Issue { number updatedAt } }
  }
}
"""

# closedByPullRequestsReferences resolves every PR whose body/commits name a closing keyword
# for this issue, open or merged — GitHub's own keyword parser, not a body-text regex, so a
# second competing PR shows up here too. ClosedEvent's `closer` union names the PR or commit
# that actually closed it, when it did.
_ISSUE_FIELDS = """
      number
      title
      url
      body
      author { login }
      authorAssociation
      createdAt
      updatedAt
      reactions { totalCount }
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
"""
_ISSUE_DETAIL_QUERY = ("query($owner: String!, $name: String!, $number: Int!) {\n"
                       "  repository(owner: $owner, name: $name) {\n    issue(number: $number) {"
                       + _ISSUE_FIELDS + "    }\n  }\n}\n")


def _fetch_issue_one(owner: str, name: str, number: int) -> dict[str, Any]:
    data = _graphql(_ISSUE_DETAIL_QUERY, {"owner": owner, "name": name, "number": number})
    return _issue_record(data["repository"]["issue"], number)


def _fetch_issues(owner: str, name: str, numbers: list[int]) -> tuple[list[dict[str, Any]], list[str]]:
    return _fetch_batched("issue", _ISSUE_FIELDS, _issue_record, _fetch_issue_one, owner, name, numbers)


def _issue_record(issue: Optional[dict[str, Any]], number: int) -> dict[str, Any]:
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
        "updated_at": issue.get("updatedAt"),
        "reactions": (issue.get("reactions") or {}).get("totalCount", 0),
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
    "number", "title", "url", "author", "author_association", "created_at", "updated_at", "reactions", "state",
    "state_reason", "milestone", "milestone_open", "milestone_due_on", "labels",
    "labels_truncated", "assignees", "assignees_truncated", "comment_count", "closing_prs",
    "closing_prs_truncated", "closed_by", "reopened_since_closed", "parent", "sub_issues",
    "tracked_in_issues",
)


def _issue_digest(issue: dict[str, Any]) -> dict[str, Any]:
    if "error" in issue:
        return issue
    return {k: issue[k] for k in _ISSUE_DIGEST_FIELDS if k in issue}


def collect_issues(repo: str, search_query: str, limit: int = 15) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Full records for the issues a search finds, cached: (records, total_count, errors). A cached record whose
    updatedAt has not moved is reused."""
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)
    full_search = f"repo:{repo} is:issue {search_query}".strip()
    search_result = _graphql(_ISSUE_SEARCH_QUERY, {"search": full_search, "first": min(limit, 100)})["search"]
    issues: list[dict[str, Any]] = []
    to_fetch = []
    for h in (n for n in search_result["nodes"] if n):
        rec = cached_issue(repo, h["number"], h.get("updatedAt"))
        if rec is not None:
            issues.append(rec)
        else:
            to_fetch.append(h["number"])
    fetched, errors = _fetch_issues(owner, name, to_fetch)
    issues += fetched
    _remember_issues(repo, issues)
    issues.sort(key=lambda i: i.get("number", 0))
    return issues, search_result["issueCount"], [f"Issue {e}" for e in errors]


def issue_records(repo: str, numbers: list[int], reuse: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    if "/" not in repo:
        raise FetchError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)
    issues, to_fetch = [], []
    for n in numbers:
        rec = cached_issue(repo, n) if reuse else None
        (issues.append(rec) if rec is not None else to_fetch.append(n))
    fetched, errors = _fetch_issues(owner, name, to_fetch)
    issues += fetched
    _remember_issues(repo, issues)
    issues.sort(key=lambda i: i.get("number", 0))
    return issues, [f"Issue {e}" for e in errors]


def fetch_issue_candidates(
    repo: str, search_query: str, limit: int = 15, detail: str = "full"
) -> dict[str, Any]:
    issues, total_count, errors = collect_issues(repo, search_query, limit)
    if detail == "digest":
        issues = [_issue_digest(i) for i in issues]
    return fit({
        "total_count": total_count,
        "fetched": len(issues),
        "truncated": total_count > len(issues),
        "issues": issues,
        "errors": errors,
    }, "issues", _issue_digest)


def fetch_issue_details(repo: str, issue_numbers: list[int]) -> dict[str, Any]:
    """Phase 2 of the digest/full split: full detail (body, comments) for an explicit list of
    issue numbers already chosen by a digest-ranked pass — skips the search step entirely."""
    issues, errors = issue_records(repo, issue_numbers)
    return fit({"fetched": len(issues), "issues": issues, "errors": errors}, "issues", _issue_digest)


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
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(pr_numbers))) as pool:
        futures = {pool.submit(_get_patch_for_file, owner, name, n, path): n for n in pr_numbers}
        for future in concurrent.futures.as_completed(futures):
            by_pr[futures[future]] = future.result()

    return {"path": path, "by_pr": by_pr}


# -- for the engine: data keyed by a commit, so it is fetched once and reused until the commit moves -------------

def pr_patches(repo: str, number: int, head_oid: Optional[str]) -> dict[str, Optional[str]]:
    """{path: patch} for one PR, from REST (GraphQL has no patch field). A patch is None for a binary file or one too
    large for GitHub to show. Cached by head commit; without one (an old record) it is fetched every time."""
    key = ("patches", repo.lower(), number, head_oid)
    if head_oid and key in _BY_COMMIT:
        return _BY_COMMIT[key]
    owner, name = repo.split("/", 1)
    out: dict[str, Optional[str]] = {}
    for page in range(1, 4):              # 300 files; past that the profile from line counts is what there is
        files = _rest_get(f"repos/{owner}/{name}/pulls/{number}/files?per_page=100&page={page}")
        for f in files:
            out[f.get("filename")] = f.get("patch")
        if len(files) < 100:
            break
    if head_oid:
        _BY_COMMIT[key] = out
    return out


def patches_for(repo: str, records: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Optional[str]]], list[str]]:
    """Patches for several PRs at once: ({number: {path: patch}}, errors)."""
    out: dict[int, dict[str, Optional[str]]] = {}
    errors: list[str] = []
    if not records:
        return out, errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(_BATCH_WORKERS, len(records))) as pool:
        futures = {pool.submit(pr_patches, repo, r["number"], r.get("head_oid")): r["number"] for r in records}
        for future in concurrent.futures.as_completed(futures):
            try:
                out[futures[future]] = future.result()
            except FetchError as exc:
                errors.append(f"patches for #{futures[future]}: {exc}")
    return out, errors


def file_text(repo: str, path: str, ref: Optional[str]) -> Optional[str]:
    """A file's text at `ref`, or None when it does not exist there. Cached by ref when ref is a commit."""
    key = ("file", repo.lower(), path, ref)
    if ref and key in _BY_COMMIT:
        return _BY_COMMIT[key]
    import base64
    owner, name = repo.split("/", 1)
    try:
        data = _rest_get(f"repos/{owner}/{name}/contents/{urllib.parse.quote(path)}" + (f"?ref={ref}" if ref else ""))
    except NotFound:
        data = None
    text = None
    if isinstance(data, dict) and data.get("encoding") == "base64":
        text = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
    if ref:
        _BY_COMMIT[key] = text
    return text


def codeowners(repo: str, rules: Rules, ref: Optional[str]) -> tuple[list, Optional[str]]:
    """(parsed CODEOWNERS rules, the path they came from), from the default branch. Where GitHub looks: the pack's
    path, then .github/, the root and docs/."""
    from .engine.owners import parse_codeowners

    for path in dict.fromkeys([rules.codeowners or ".github/CODEOWNERS", ".github/CODEOWNERS", "CODEOWNERS",
                               "docs/CODEOWNERS"]):
        text = file_text(repo, path, ref)
        if text is not None:
            return parse_codeowners(text), path
    return [], None


def agent_doc_paths(repo: str, ref: Optional[str]) -> list[str]:
    """Every AGENTS.md in the repository at `ref` (one tree read, cached by commit)."""
    key = ("agent-docs", repo.lower(), ref)
    if ref and key in _BY_COMMIT:
        return _BY_COMMIT[key]
    owner, name = repo.split("/", 1)
    tree = _rest_get(f"repos/{owner}/{name}/git/trees/{ref or 'HEAD'}?recursive=1")
    paths = sorted(t["path"] for t in tree.get("tree") or []
                   if t.get("type") == "blob" and re.search(r"(^|/)AGENTS\.md$", t.get("path") or "", re.I))
    if ref:
        _BY_COMMIT[key] = paths
    return paths


_MILESTONES_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    milestones(states: OPEN, first: 10, orderBy: {field: DUE_DATE, direction: ASC}) {
      nodes { title dueOn url issues(states: OPEN) { totalCount } pullRequests(states: OPEN) { totalCount } }
    }
  }
}
"""

_COUNT_QUERY = """
query($search: String!) { search(query: $search, type: ISSUE, first: 1) { issueCount } }
"""


def milestones(repo: str) -> list[dict[str, Any]]:
    """Open milestones, soonest due first; those with no due date last."""
    owner, name = repo.split("/", 1)
    nodes = _graphql(_MILESTONES_QUERY, {"owner": owner, "name": name})["repository"]["milestones"]["nodes"]
    out = [{"title": m["title"], "due_on": m.get("dueOn"), "url": m.get("url"),
            "open_issues": m["issues"]["totalCount"], "open_prs": m["pullRequests"]["totalCount"]} for m in nodes]
    return sorted(out, key=lambda m: (m["due_on"] is None, m["due_on"] or "", m["title"]))


def count(repo: str, qualifiers: str) -> int:
    return _graphql(_COUNT_QUERY, {"search": f"repo:{repo} {qualifiers}"})["search"]["issueCount"]
