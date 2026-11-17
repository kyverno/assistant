"""The three views the model reads, each kept under a fixed character budget (about four characters a token):

L1 queue  the top items for a focus, one line each with reasons      QUEUE_BUDGET
L2 card   one PR or issue: change profile, issue, relations, owners   CARD_BUDGET
L3 code   the patches of the files that matter most, and the docs     CODE_BUDGET

Free text from GitHub (bodies, comments, patches) is written by anyone, so it is put under keys named untrusted_*.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from . import rank

QUEUE_BUDGET = 12_000
CARD_BUDGET = 3_200
CODE_BUDGET = 10_000
LINES_PER_FILE = 120
LINES_PER_PR = 400


def size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


def _clip(text: Optional[str], n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def fit(view: dict, budget: int, steps: list[Callable[[dict], bool]]) -> dict:
    """Apply each trimming step in turn until the view fits. A step returns False when it has nothing left to trim, and
    the next step takes over. The last resort clips every string."""
    for step in steps:
        while size(view) > budget:
            if not step(view):
                break
        if size(view) <= budget:
            return view
    limit = 200
    while size(view) > budget and limit > 20:
        view = json.loads(json.dumps(view), object_hook=lambda d: {k: _clip(v, limit) if isinstance(v, str) else v
                                                                   for k, v in d.items()})
        limit //= 2
    return view


# -- L1 ------------------------------------------------------------------------------------------------------------

def relations_line(n: int, relations: dict[int, list[str]]) -> str:
    return "; ".join(relations.get(n, [])[:3])


def queue_line(e: dict, record: dict, profile: Optional[dict], relations: dict[int, list[str]]) -> dict[str, Any]:
    r = e["readiness"]
    line: dict[str, Any] = {
        "n": e["number"],
        "title": _clip(record.get("title"), 90),
        "priority": rank.priority_label(e),
        "why": rank.explain(e),
        "next": r["next"],
    }
    other = [b for b in r.get("blockers") or [] if b not in r["next"]]
    if other:
        line["blockers"] = ", ".join(other)
    if profile:
        line["change"] = profile["summary"]
    if e.get("risk"):
        line["risk"] = ", ".join(e["risk"])
    rel = relations_line(e["number"], relations)
    if rel:
        line["relations"] = rel
    if record.get("author"):
        line["by"] = record["author"] + (" (first-time)" if record.get("author_association") in
                                         ("FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER") else "")
    line["age_days"] = e["age"]
    return line


def queue_view(head: dict, lines: list[dict], tail: dict) -> dict:
    view = {**head, "items": lines, **tail}

    def drop_item(v: dict) -> bool:
        if len(v["items"]) <= 3:
            return False
        v.setdefault("not_shown", []).insert(0, v["items"].pop()["n"])
        return True

    def drop_change(v: dict) -> bool:
        for item in reversed(v["items"]):
            if "change" in item:
                del item["change"]
                return True
        return False

    def drop_brief(v: dict) -> bool:
        for k in ("pack", "now"):
            if k in v:
                v[k] = "left out to fit; call queue again for it"
                return True
        return False

    return fit(view, QUEUE_BUDGET, [drop_item, drop_change, drop_brief])


# -- L2 ------------------------------------------------------------------------------------------------------------

def pr_card(record: dict, e: Optional[dict], profile: dict, owners: dict[str, int], yours: bool,
            relations: list[str]) -> dict:
    issues = []
    for i in record.get("closing_issues") or []:
        issues.append({
            "n": i["number"], "title": _clip(i.get("title"), 90), "state": i.get("state"),
            "labels": i.get("labels") or [], "milestone": i.get("milestone"), "reactions": i.get("reactions"),
            "untrusted_excerpt": _clip(i.get("body_excerpt"), 300),
            "other_prs": [f"#{o['number']} {o['state']}" for o in i.get("other_prs") or []],
        })
    copilot = record.get("copilot_review") or {}
    card: dict[str, Any] = {
        "n": record["number"], "title": record.get("title"), "url": record.get("url"),
        "author": record.get("author"), "author_association": record.get("author_association"),
        "base": record.get("base_branch"), "created_at": record.get("created_at"),
        "labels": record.get("labels") or [],
        "change": {k: v for k, v in profile.items() if k not in ("touched", "summary")},
        "closing_issues": issues,
        "review": {
            "decision": record.get("review_decision"), "unresolved_threads": record.get("unresolved_review_threads"),
            "bot_approved": record.get("bot_approved"),
            **({"copilot": copilot.get("verdict")} if copilot.get("verdict") else {}),
        },
        "ci": record.get("ci_state"),
        **({"failing_checks": record["failing_checks"]} if record.get("failing_checks") else {}),
        **({"gate_checks": record["gate_checks"]} if record.get("gate_checks") else {}),
        "gate_blocked": record.get("gate_blocked", False),
        "owners": dict(list(owners.items())[:5]),
        "yours": yours,
        "relations": relations,
        **({"external_references": [f"#{r['number']} {r.get('kind', '')} {r.get('state')}".replace("  ", " ")
                                    for r in record["external_references"]]}
           if record.get("external_references") else {}),
        "untrusted_body": _clip(record.get("body"), 600),
    }
    if e:
        card["priority"] = rank.priority_label(e)
        card["why"] = rank.explain(e)
        card["next"] = e["readiness"]["next"]
        card["blockers"] = e["readiness"]["blockers"]
        card["risk"] = e["risk"]

    def shorter_body(v: dict) -> bool:
        if len(v["untrusted_body"]) <= 120:
            return False
        v["untrusted_body"] = _clip(v["untrusted_body"], len(v["untrusted_body"]) // 2)
        return True

    def shorter_issues(v: dict) -> bool:
        for i in v["closing_issues"]:
            if len(i["untrusted_excerpt"]) > 80:
                i["untrusted_excerpt"] = _clip(i["untrusted_excerpt"], 80)
                return True
        if len(v["closing_issues"]) > 2:
            v["closing_issues"] = v["closing_issues"][:2] + [{"more": len(v["closing_issues"]) - 2}]
            return True
        return False

    def fewer_lists(v: dict) -> bool:
        ch = v["change"]
        if len(ch.get("functions") or []) > 5:
            ch["functions"] = ch["functions"][:5]
            return True
        if len(v["relations"]) > 3:
            v["relations"] = v["relations"][:3]
            return True
        if len(v["labels"]) > 8:
            v["labels"] = v["labels"][:8]
            return True
        return False

    return fit(card, CARD_BUDGET, [shorter_body, shorter_issues, fewer_lists])


def issue_card(issue: dict, e: Optional[dict]) -> dict:
    card: dict[str, Any] = {
        "n": issue["number"], "title": issue.get("title"), "url": issue.get("url"), "state": issue.get("state"),
        "author": issue.get("author"), "created_at": issue.get("created_at"), "labels": issue.get("labels") or [],
        "milestone": issue.get("milestone"), "assignees": issue.get("assignees") or [],
        "reactions": issue.get("reactions"), "comments": issue.get("comment_count"),
        "linked_prs": [f"#{p['number']} {p['state']}" for p in issue.get("closing_prs") or []],
        **({"parent": f"#{issue['parent']['number']}"} if issue.get("parent") else {}),
        "untrusted_body": _clip(issue.get("body"), 900),
        "untrusted_recent_comments": [
            {"by": c.get("author"), "at": (c.get("created_at") or "")[:10], "text": _clip(c.get("body"), 240)}
            for c in (issue.get("recent_comments") or [])[-3:]
        ],
    }
    if e:
        card["priority"] = rank.priority_label(e)
        card["why"] = rank.explain(e)
        card["next"] = e["readiness"]["next"]

    def shorter(v: dict) -> bool:
        if v["untrusted_recent_comments"]:
            v["untrusted_recent_comments"].pop(0)
            return True
        if len(v["untrusted_body"]) > 200:
            v["untrusted_body"] = _clip(v["untrusted_body"], len(v["untrusted_body"]) // 2)
            return True
        return False

    return fit(card, CARD_BUDGET, [shorter])


# -- L3 ------------------------------------------------------------------------------------------------------------

_CLASS_ORDER = {"API_SURFACE": 0, "CRITICAL": 1, "STANDARD": 2, "TEST_ONLY": 3, "GENERATED": 4}


def _trim_patch(patch: str, max_lines: int) -> tuple[str, int]:
    lines = patch.splitlines()
    if len(lines) <= max_lines:
        return patch, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def code_view(record: dict, patches: dict[str, Optional[str]], rules, docs: Optional[dict] = None) -> dict:
    """The patches that say most about the change: API surface, then critical, then the rest; tests and generated
    output last. At most LINES_PER_FILE per file and LINES_PER_PR in all; files left out are named."""
    from .profile import is_test

    files = record.get("files") or [{"path": p, "additions": 0, "deletions": 0} for p in record.get("changed_files") or []]

    def order(f: dict) -> tuple:
        cls = "TEST_ONLY" if is_test(f["path"], rules) else rules.classify(f["path"])
        return _CLASS_ORDER.get(cls, 2), -((f.get("additions") or 0) + (f.get("deletions") or 0)), f["path"]

    out, left, budget_lines = [], [], LINES_PER_PR
    for f in sorted(files, key=order):
        patch = patches.get(f["path"])
        cls = rules.classify(f["path"])
        if cls == "GENERATED" or not patch or budget_lines <= 0:
            left.append(f["path"] + (" (generated)" if cls == "GENERATED" else " (no patch)" if not patch else ""))
            continue
        text, cut = _trim_patch(patch, min(LINES_PER_FILE, budget_lines))
        budget_lines -= len(text.splitlines())
        out.append({"path": f["path"], "class": cls, "untrusted_patch": text, **({"lines_cut": cut} if cut else {})})
    view: dict[str, Any] = {"n": record["number"], "head": record.get("head_oid"), "files": out}
    if left:
        view["not_shown"] = left
    if docs:
        view["docs"] = docs

    def drop_file(v: dict) -> bool:
        if len(v["files"]) <= 1:
            return False
        v.setdefault("not_shown", []).insert(0, v["files"].pop()["path"])
        return True

    def shorter_docs(v: dict) -> bool:
        d = v.get("docs")
        if not d or len(d.get("untrusted_excerpt", "")) <= 300:
            return False
        d["untrusted_excerpt"] = _clip(d["untrusted_excerpt"], len(d["untrusted_excerpt"]) // 2)
        return True

    def shorter_patch(v: dict) -> bool:
        f = v["files"][0] if v["files"] else None
        if not f or len(f["untrusted_patch"]) < 400:
            return False
        lines = f["untrusted_patch"].splitlines()
        f["untrusted_patch"] = "\n".join(lines[: len(lines) // 2])
        f["lines_cut"] = f.get("lines_cut", 0) + len(lines) - len(lines) // 2
        return True

    return fit(view, CODE_BUDGET, [shorter_docs, drop_file, shorter_patch])
