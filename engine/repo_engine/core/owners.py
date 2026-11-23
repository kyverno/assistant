"""CODEOWNERS: the rule that owns each path. Copied from profiles/contributor/plugins/contributor/rules.py, and
tests/plugins/test_engine.py checks both give the same owners. The copy goes when the engine moves to the top level."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def _glob_to_regex(glob: str) -> str:
    i, out = 0, ""
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i : i + 2] == "**":
                i += 2
                if glob[i : i + 1] == "/":
                    i += 1
                    out += "(?:.*/)?"      # `**/` matches zero or more directories
                else:
                    out += ".*"
                continue
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    return out


def parse_codeowners(text: str) -> List[Tuple[str, List[str]]]:
    rules: List[Tuple[str, List[str]]] = []
    for line in text.splitlines():
        line = line.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rules.append((parts[0], parts[1:]))
    return rules


def _codeowners_regex(pattern: str) -> str:
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    body = pattern.strip("/")
    directory = pattern.endswith("/")
    rx = _glob_to_regex(body)
    prefix = "" if anchored else "(?:.*/)?"
    # a pattern naming a directory (or a plain name) also owns everything below it
    suffix = "(?:/.*)?" if (directory or not re.search(r"[*?]", body.split("/")[-1])) else ""
    if body.endswith("**"):
        suffix = ""
    return prefix + rx + suffix


def owners_for_path(rules: List[Tuple[str, List[str]]], path: str) -> Optional[Dict[str, Any]]:
    """The last matching CODEOWNERS rule wins, as on GitHub."""
    match = None
    for pattern, owners in rules:
        if pattern == "*" or re.fullmatch(_codeowners_regex(pattern), path):
            match = {"pattern": pattern, "owners": owners}
    return match


def owners_of(rules: List[Tuple[str, List[str]]], paths: List[str]) -> Dict[str, int]:
    """{owner: how many of `paths` they own}, most first. A catch-all `*` rule counts too: it is who GitHub asks."""
    counts: Dict[str, int] = {}
    for p in paths:
        m = owners_for_path(rules, p)
        for o in (m or {}).get("owners", []):
            counts[o] = counts.get(o, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def owns(owner_counts: Dict[str, int], login: Optional[str]) -> bool:
    """Whether `login` is named directly. Team membership needs read:org, which the token may not have, so a team
    owner is shown as the team and never counted as the maintainer's."""
    return bool(login) and f"@{login}".lower() in {o.lower() for o in owner_counts}
