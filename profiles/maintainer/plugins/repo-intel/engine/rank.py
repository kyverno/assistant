"""Priority, readiness, risk and close candidates for PRs and issues, each with the reasons behind it.

The rules (docs/architecture.md has the longer form):
- Priority and readiness are separate. A blocker never raises priority; it decides the next action and who takes it.
- Priority, in order: severity (the pack's severity_labels on the closing issue, else the PR's own labels; wording in
  the text only when no label says anything, marked inferred), then urgency (a release label on the closing issue, the
  milestone's due date), then reactions. A PR inherits the priority of a higher-priority PR that has to merge after it.
- Order: prefer first, then priority, then what you can act on now before what waits on someone else, then older,
  then smaller, then the number, so the same input always gives the same order.
- Risk is shown, never ranked. Close candidates are a separate list, never closed by anything here.
- `prefer` and `avoid` are the maintainer's stated priorities (said this session, or recalled from memory). They beat
  everything else: a preferred item goes first, an avoided one is left out and counted.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

SEVERITY_NAMES = ("critical", "high", "medium", "unrated", "low")
_UNRATED = 3
_INFERRED = (
    (0, re.compile(r"\bCVE-\d{4}-\d+|\bvulnerab|\bsecurity (?:issue|fix|flaw)|\bpanic|\bcrash|\bsegfault|"
                   r"nil pointer|data loss|\bdeadlock|\bOOM\b|out of memory", re.I)),
    (1, re.compile(r"\bregression\b|no longer works|used to work|stopped working|broke in v?\d", re.I)),
)
URGENT_DAYS = 14
# Issue lifecycle labels from the shared issue management (kyverno/assistant#63), and common spellings.
WAITING_LABELS = ("needs-info", "needs-more-info", "triage:needs-repro", "waiting-on-reporter")
STALE_LABELS = ("stale", "stale-issue", "lifecycle/stale")
RESOLVED_LABELS = ("possibly-resolved",)


def _days(iso: Optional[str], now: datetime) -> Optional[int]:
    if not iso:
        return None
    try:
        return (datetime.fromisoformat(iso.replace("Z", "+00:00")) - now).days
    except ValueError:
        return None


def _age(iso: Optional[str], now: datetime) -> int:
    d = _days(iso, now)
    return -d if d is not None else 0


# -- severity and urgency ----------------------------------------------------------------------------------------

def severity_of_labels(labels: list[str], rules) -> Optional[tuple[int, str]]:
    lower = {l.lower(): l for l in labels}
    for tier, names in rules.severity_labels:
        for name in names:
            if name.lower() in lower:
                return {"critical": 0, "high": 1, "medium": 2, "low": 4}[tier], lower[name.lower()]
    return None


def severity_of_text(text: str) -> Optional[tuple[int, str]]:
    for tier, rx in _INFERRED:
        m = rx.search(text or "")
        if m:
            return tier, m.group(0)
    return None


def pr_severity(pr: dict, rules) -> dict[str, Any]:
    """{tier, name, why, inferred}. The closing issues' labels first: that is where a project records severity."""
    best: Optional[tuple[int, str]] = None
    for issue in pr.get("closing_issues") or []:
        hit = severity_of_labels(issue.get("labels") or [], rules)
        if hit and (best is None or hit[0] < best[0]):
            best, where = hit, f"#{issue['number']}"
    if best is None:
        hit = severity_of_labels(pr.get("labels") or [], rules)
        if hit:
            best, where = hit, "the PR"
    if best is not None:
        return {"tier": best[0], "name": SEVERITY_NAMES[best[0]], "why": f"{best[1]} on {where}", "inferred": False}
    texts = [pr.get("title") or ""] + [f"{i.get('title') or ''} {i.get('body_excerpt') or ''}"
                                       for i in pr.get("closing_issues") or []]
    hit = severity_of_text(" ".join(texts))
    if hit:
        return {"tier": hit[0], "name": SEVERITY_NAMES[hit[0]], "why": f"'{hit[1]}' in the text (inferred)",
                "inferred": True}
    return {"tier": _UNRATED, "name": "unrated", "why": "", "inferred": False}


def urgency(labels_by_source: list[tuple[str, list[str]]], milestones: list[tuple[str, Optional[str]]], rules,
            now: datetime) -> dict[str, Any]:
    """{bucket, why}: 0 urgent (a top-two release label, or a milestone due within URGENT_DAYS or overdue), 1 scheduled
    (any release label or milestone), 2 neither."""
    order = [l.lower() for l in rules.issue_urgency_labels]
    best: Optional[tuple[int, str]] = None
    for where, labels in labels_by_source:
        for l in labels:
            if l.lower() in order:
                i = order.index(l.lower())
                if best is None or i < best[0]:
                    best = (i, f"{l} on {where}")
    reasons, bucket = [], 2
    if best is not None:
        reasons.append(best[1])
        bucket = 0 if best[0] < 2 else 1
    soonest: Optional[tuple[int, str]] = None
    for title, due in milestones:
        if not title:
            continue
        d = _days(due, now)
        if d is None:
            bucket = min(bucket, 1)
            soonest = soonest or (10_000, f"milestone {title}")
        elif soonest is None or d < soonest[0]:
            soonest = (d, f"milestone {title} " + (f"overdue by {-d} days" if d < 0 else f"due in {d} days"))
    if soonest:
        reasons.append(soonest[1])
        if soonest[0] != 10_000:
            bucket = min(bucket, 0 if soonest[0] <= URGENT_DAYS else 1)
    return {"bucket": bucket, "why": " · ".join(reasons)}


def pr_urgency(pr: dict, rules, now: datetime) -> dict[str, Any]:
    issues = [i for i in pr.get("closing_issues") or [] if i.get("state", "OPEN") == "OPEN"]
    labels = [(f"#{i['number']}", i.get("labels") or []) for i in issues]
    milestones = [(pr.get("milestone"), pr.get("milestone_due_on"))]
    milestones += [(i.get("milestone"), i.get("milestone_due_on")) for i in issues if i.get("milestone_open")]
    return urgency(labels, milestones, rules, now)


# -- readiness ---------------------------------------------------------------------------------------------------

def pr_readiness(pr: dict, rules, login: Optional[str]) -> dict[str, Any]:
    """{next, who, blockers, actionable}. who is "you", "author", "ci" or "gate"; actionable means you can do the next
    step now. The gate blocks merging, never reviewing."""
    author_side: list[str] = []
    if pr.get("ci_state") == "FAILURE":
        failing = pr.get("failing_checks") or []
        author_side.append("CI failing" + (f" ({', '.join(failing[:3])})" if failing else ""))
    if pr.get("merge_state") == "DIRTY":
        author_side.append("merge conflicts")
    if pr.get("review_decision") == "CHANGES_REQUESTED":
        author_side.append("changes requested")
    threads = pr.get("unresolved_review_threads") or 0
    if threads:
        author_side.append(f"{threads} unresolved review thread{'s' if threads > 1 else ''}")
    yours_to_do: list[str] = []
    for label in pr.get("labels") or []:
        low = label.lower()
        if "workflow-approval" in low:
            yours_to_do.append("approve the workflow run")
        elif label in rules.attention_labels and "conflict" not in low:
            (yours_to_do if "hold" in low else author_side).append(f"labelled {label}")
    gate = "merging blocked by the gate" if pr.get("gate_blocked") else None
    blockers = author_side + yours_to_do + ([gate] if gate else [])

    if login and (pr.get("author") or "").lower() == login.lower():
        return {"next": "your own PR" + (": " + ", ".join(author_side) if author_side else ": waiting for review"),
                "who": "author", "blockers": blockers, "actionable": False}
    if yours_to_do:
        return {"next": yours_to_do[0], "who": "you", "blockers": blockers, "actionable": True}
    if author_side:
        return {"next": "waiting on author", "who": "author", "blockers": blockers, "actionable": False}
    if pr.get("ci_state") == "PENDING":
        return {"next": "CI running", "who": "ci", "blockers": blockers + ["CI running"], "actionable": False}
    if pr.get("review_decision") == "APPROVED":
        if gate:
            return {"next": "approved; waiting on the gate", "who": "gate", "blockers": blockers, "actionable": False}
        if pr.get("merge_state") == "BEHIND":
            return {"next": "approved; update the branch, then merge", "who": "you", "blockers": blockers,
                    "actionable": True}
        return {"next": "ready to merge", "who": "you", "blockers": blockers, "actionable": True}
    return {"next": "review", "who": "you", "blockers": blockers, "actionable": True}


def pr_risk(profile: dict) -> list[str]:
    """How carefully to review, not how soon."""
    out = []
    for cls in ("API_SURFACE", "CRITICAL"):
        c = profile["by_class"].get(cls)
        if c and c["lines"]:
            out.append(f"{c['lines']} lines in {cls} code")
    if profile["code_without_tests"]:
        out.append("no tests")
    if profile["generated"] and profile["generated"] != "in sync":
        out.append(profile["generated"])
    if profile.get("truncated"):
        out.append(profile["truncated"])
    return out


def pr_close_candidate(pr: dict, now: datetime) -> Optional[str]:
    issues = pr.get("closing_issues") or []
    for i in issues:
        merged = [o["number"] for o in i.get("other_prs") or [] if o.get("state") == "MERGED"]
        if merged:
            return f"#{merged[0]} already merged a fix for #{i['number']}"
    if issues and all(i.get("state") == "CLOSED" for i in issues):
        return "its issue" + ("s are" if len(issues) > 1 else f" #{issues[0]['number']} is") + " already closed"
    quiet = _age(pr.get("updated_at"), now)
    if any("stale" in l.lower() for l in pr.get("labels") or []) and quiet >= 30:
        return f"labelled stale and quiet for {quiet} days"
    if pr.get("merge_state") == "DIRTY" and quiet >= 60:
        return f"merge conflicts and quiet for {quiet} days"
    return None


# -- prefer and avoid --------------------------------------------------------------------------------------------

def facts_for_match(item: dict) -> dict[str, Any]:
    labels = list(item.get("labels") or [])
    for i in item.get("closing_issues") or []:
        labels += i.get("labels") or []
    return {
        "number": item.get("number"),
        "author": (item.get("author") or "").lower(),
        "labels": {l.lower() for l in labels},
        "paths": [f["path"] if isinstance(f, dict) else f for f in (item.get("files") or item.get("changed_files") or [])],
        "milestones": {m.lower() for m in [item.get("milestone")] + [i.get("milestone") for i in item.get("closing_issues") or []] if m},
        "functions": [fn.lower() for fn in (item.get("_functions") or [])],
    }


def matches(term: str, facts: dict) -> bool:
    """A term names a number (#123), an author (@login or author:login), a label (label:x or the bare name), a
    milestone, a path or path prefix, or a function."""
    t = term.strip().lower()
    if not t:
        return False
    if t.lstrip("#").isdigit():
        return int(t.lstrip("#")) == facts["number"]
    if t.startswith(("@", "author:")):
        return t.split(":", 1)[-1].lstrip("@") == facts["author"]
    if t.startswith("label:"):
        return t[6:].strip('"') in facts["labels"]
    if t.startswith("milestone:"):
        return t[10:].strip('"') in facts["milestones"]
    if t in facts["labels"] or t in facts["milestones"]:
        return True
    tp = t.strip("/")
    if any(p.lower() == tp or p.lower().startswith(tp + "/") for p in facts["paths"]):
        return True
    return any(t == fn or fn.endswith("." + t) for fn in facts["functions"])


def _first_match(terms: list[str], facts: dict) -> Optional[str]:
    return next((t for t in terms if matches(t, facts)), None)


# -- ranking -----------------------------------------------------------------------------------------------------

def rank_prs(prs: list[dict], rules, *, login: Optional[str] = None, prefer: list[str] = (), avoid: list[str] = (),
             edges: list[tuple[int, int, str]] = (), profiles: Optional[dict[int, dict]] = None,
             now: Optional[datetime] = None) -> dict[str, Any]:
    """{ranked, close_candidates, avoided}. Each ranked entry: number, severity, urgency, readiness, risk, preferred,
    inherits (the PR whose priority it carries), and the sort key's parts."""
    now = now or datetime.now(timezone.utc)
    profiles = profiles or {}
    entries: dict[int, dict] = {}
    close, avoided = [], []
    for pr in prs:
        if "error" in pr:
            continue
        n = pr["number"]
        facts = facts_for_match(pr)
        hit = _first_match(list(avoid), facts)
        if hit:
            avoided.append({"number": n, "why": f"you avoid {hit}"})
            continue
        why_close = pr_close_candidate(pr, now)
        if why_close:
            close.append({"number": n, "why": why_close})
            continue
        sev, urg = pr_severity(pr, rules), pr_urgency(pr, rules, now)
        entries[n] = {
            "number": n,
            "severity": sev,
            "urgency": urg,
            "readiness": pr_readiness(pr, rules, login),
            "risk": pr_risk(profiles[n]) if n in profiles else [],
            "preferred": _first_match(list(prefer), facts),
            "band": (sev["tier"], urg["bucket"]),
            "inherits": None,
            "reactions": max([i.get("reactions") or 0 for i in pr.get("closing_issues") or []] + [0]),
            "age": _age(pr.get("created_at"), now),
            "size": sum((f.get("additions") or 0) + (f.get("deletions") or 0) for f in pr.get("files") or [])
                    or (pr.get("size") or {}).get("additions", 0),
        }

    # A PR that must merge before another carries the later one's priority when that is higher.
    after: dict[int, set[int]] = {}
    for a, b, _kind in edges:
        if a in entries and b in entries:
            after.setdefault(a, set()).add(b)
    for n in sorted(entries):
        stack, seen = list(after.get(n, ())), set()
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            stack.extend(after.get(m, ()))
            if entries[m]["band"] < entries[n]["band"]:
                entries[n]["band"], entries[n]["inherits"] = entries[m]["band"], m

    def key(e: dict) -> tuple:
        return (e["preferred"] is None, e["band"], not e["readiness"]["actionable"], -(min(e["reactions"], 20) // 5),
                -e["age"], e["size"], e["number"])

    ranked = sorted(entries.values(), key=key)
    # Keep every hard dependency in order: a PR that must merge first is never listed after its dependant.
    ranked = _respect_edges(ranked, [(a, b) for a, b, _k in edges if a in entries and b in entries])
    return {"ranked": ranked, "close_candidates": sorted(close, key=lambda c: c["number"]),
            "avoided": sorted(avoided, key=lambda c: c["number"])}


def _respect_edges(ranked: list[dict], edges: list[tuple[int, int]]) -> list[dict]:
    """Stable: each step takes the highest-ranked PR none of whose must-merge-first PRs is still waiting."""
    before: dict[int, set[int]] = {}
    for a, b in edges:
        before.setdefault(b, set()).add(a)
    out: list[dict] = []
    pending = list(ranked)
    while pending:
        waiting = {e["number"] for e in pending}
        pick = next((i for i, e in enumerate(pending) if not before.get(e["number"], set()) & waiting), None)
        if pick is None:            # a cycle: keep the ranked order for the rest
            return out + pending
        out.append(pending.pop(pick))
    return out


def explain(e: dict) -> str:
    """The reasons for one position, joined: what the maintainer reads next to the number."""
    parts = []
    if e.get("preferred"):
        parts.append(f"you prefer {e['preferred']}")
    if e["severity"]["why"]:
        parts.append(e["severity"]["why"])
    if e["urgency"]["why"]:
        parts.append(e["urgency"]["why"])
    if e.get("inherits"):
        parts.append(f"must merge before #{e['inherits']}")
    if e.get("reactions", 0) >= 5:
        parts.append(f"{e['reactions']} reactions")
    return " · ".join(parts) or "no severity or milestone signal"


def priority_label(e: dict) -> str:
    sev = SEVERITY_NAMES[e["band"][0]]
    return sev + {0: " · urgent", 1: " · scheduled", 2: ""}[e["band"][1]]


# -- issues ------------------------------------------------------------------------------------------------------

def issue_state(issue: dict, login: Optional[str], now: datetime) -> dict[str, Any]:
    """{next, who, actionable, close}: what the issue needs, from the lifecycle labels and its linked PRs."""
    labels = [l.lower() for l in issue.get("labels") or []]
    prs = issue.get("closing_prs") or []
    open_prs = [p["number"] for p in prs if p.get("state") == "OPEN"]
    merged = [p["number"] for p in prs if p.get("state") == "MERGED"]
    if any(l in RESOLVED_LABELS for l in labels):
        return {"next": "confirm it is resolved", "who": "you", "actionable": True,
                "close": "labelled possibly resolved" + (f" (#{merged[0]} merged)" if merged else "")}
    if merged and not open_prs:
        return {"next": "confirm it is resolved", "who": "you", "actionable": True,
                "close": f"#{merged[0]} merged and the issue is still open"}
    quiet = _age(issue.get("updated_at"), now)
    if any(l in STALE_LABELS for l in labels) and quiet >= 14:
        return {"next": "close or revive", "who": "you", "actionable": True,
                "close": f"labelled stale and quiet for {quiet} days"}
    if any(l in WAITING_LABELS for l in labels):
        return {"next": "waiting on the reporter", "who": "reporter", "actionable": False, "close": None}
    if len(open_prs) > 1:
        return {"next": "competing PRs " + ", ".join(f"#{n}" for n in open_prs) + ": pick one", "who": "you",
                "actionable": True, "close": None}
    if open_prs:
        return {"next": f"PR #{open_prs[0]} open: review it", "who": "you", "actionable": True, "close": None}
    if any(l == "triage" or l.startswith("triage:") or l == "needs-triage" for l in labels):
        return {"next": "triage", "who": "you", "actionable": True, "close": None}
    assignees = issue.get("assignees") or []
    if assignees:
        mine = login and login.lower() in (a.lower() for a in assignees)
        return {"next": "yours, no PR yet" if mine else f"assigned to {', '.join(assignees[:2])}, no PR yet",
                "who": "you" if mine else "assignee", "actionable": bool(mine), "close": None}
    return {"next": "needs an owner", "who": "you", "actionable": True, "close": None}


def rank_issues(issues: list[dict], rules, *, login: Optional[str] = None, prefer: list[str] = (),
                avoid: list[str] = (), now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    entries, close, avoided = [], [], []
    for issue in issues:
        if "error" in issue:
            continue
        n = issue["number"]
        facts = facts_for_match(issue)
        hit = _first_match(list(avoid), facts)
        if hit:
            avoided.append({"number": n, "why": f"you avoid {hit}"})
            continue
        state = issue_state(issue, login, now)
        if state["close"]:
            close.append({"number": n, "why": state["close"]})
            continue
        hit = severity_of_labels(issue.get("labels") or [], rules)
        if hit:
            sev = {"tier": hit[0], "name": SEVERITY_NAMES[hit[0]], "why": hit[1], "inferred": False}
        else:
            t = severity_of_text(f"{issue.get('title') or ''} {(issue.get('body') or '')[:600]}")
            sev = ({"tier": t[0], "name": SEVERITY_NAMES[t[0]], "why": f"'{t[1]}' in the text (inferred)", "inferred": True}
                   if t else {"tier": _UNRATED, "name": "unrated", "why": "", "inferred": False})
        urg = urgency([("the issue", issue.get("labels") or [])],
                      [(issue.get("milestone"), issue.get("milestone_due_on"))] if issue.get("milestone_open") else [],
                      rules, now)
        entries.append({
            "number": n, "severity": sev, "urgency": urg, "readiness": state, "risk": [],
            "preferred": _first_match(list(prefer), facts), "band": (sev["tier"], urg["bucket"]), "inherits": None,
            "reactions": issue.get("reactions") or 0, "age": _age(issue.get("created_at"), now), "size": 0,
        })
    entries.sort(key=lambda e: (e["preferred"] is None, e["band"], not e["readiness"]["actionable"],
                                -(min(e["reactions"], 20) // 5), -e["age"], e["number"]))
    return {"ranked": entries, "close_candidates": sorted(close, key=lambda c: c["number"]),
            "avoided": sorted(avoided, key=lambda c: c["number"])}
