"""What a PR changes, from data GitHub already returns: per-file line counts (one GraphQL field) and, for PRs in focus,
the patches (REST), whose hunk headers and +/- lines name the functions touched.

`rules` is the organisation pack's Rules (classify, is_test_only, is_generated_input_path, input_paths, generated);
nothing here knows any organisation."""

from __future__ import annotations

import os
import re
from typing import Any, Optional

# Test files by name, for languages the pack's test_only globs may not list.
_TEST_NAME = re.compile(r"(_test\.go|_test\.py|\.test\.[jt]sx?|\.spec\.[jt]sx?|(^|/)test_[^/]+\.py)$")
_DOC = re.compile(r"\.(md|rst|adoc)$", re.I)
_HUNK = re.compile(r"^@@ [^@]* @@ ?(.*)$")
_DEFS = (
    # Go: `func (r *Type[T]) Name(` or `func Name(`
    re.compile(r"^\s*func\s+(?:\(\s*\w*\s*\*?\s*([\w.]+)(?:\[[^\]]*\])?\s*\)\s*)?([A-Za-z_]\w*)\s*[\[(]"),
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("),                       # Python
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),   # JS/TS
)
FUNCTIONS_SHOWN = 12


def is_test(path: str, rules) -> bool:
    return bool(_TEST_NAME.search(path)) or rules.is_test_only(path)


def _definition(line: str) -> Optional[str]:
    for rx in _DEFS:
        m = rx.match(line)
        if m:
            groups = [g for g in m.groups() if g]
            return ".".join(groups[-2:]) if len(groups) > 1 else groups[0]
    return None


def functions_in_patch(patch: str) -> list[str]:
    """Functions a patch touches: the one each hunk header names (the function enclosing the hunk's first line) and any
    defined on a changed line. In order of appearance, each once. Languages without a pattern here give none."""
    seen: list[str] = []
    for line in (patch or "").splitlines():
        m = _HUNK.match(line)
        if m:
            name = _definition(m.group(1))
        elif line[:1] in "+-" and not line.startswith(("+++", "---")):
            name = _definition(line[1:])
        else:
            continue
        if name and name not in seen:
            seen.append(name)
    return seen


def _files(record: dict) -> list[dict]:
    if record.get("files"):
        return record["files"]
    # An older record, or one from a query without line counts: paths only.
    return [{"path": p, "additions": 0, "deletions": 0, "change": "MODIFIED"} for p in record.get("changed_files") or []]


def change_profile(record: dict, rules, patches: Optional[dict[str, Optional[str]]] = None) -> dict[str, Any]:
    """The facts a reviewer asks first, plus `summary`, one line of them. `patches` ({path: patch}) adds functions."""
    files = _files(record)
    by_class: dict[str, dict[str, int]] = {}
    packages: dict[str, int] = {}
    tests = docs = code = 0
    touches_input = touches_generated = api_changed = False
    for f in files:
        path, lines = f["path"], (f.get("additions") or 0) + (f.get("deletions") or 0)
        cls = rules.classify(path)
        c = by_class.setdefault(cls, {"files": 0, "lines": 0})
        c["files"] += 1
        c["lines"] += lines
        if is_test(path, rules):
            tests += lines or 1
        elif _DOC.search(path):
            docs += lines or 1
        elif cls != "GENERATED":
            code += lines
            d = os.path.dirname(path) or "."
            packages[d] = packages.get(d, 0) + (lines or 1)
        touches_input |= rules.is_generated_input_path(path)
        touches_generated |= cls == "GENERATED"
        api_changed |= cls == "API_SURFACE" and lines > 0

    if touches_input and not touches_generated and rules.generated:
        generated = "inputs changed, generated output not updated"
    elif touches_generated and not touches_input and rules.input_paths and not record.get("is_dependabot"):
        generated = "generated output changed without its inputs"
    else:
        generated = "in sync" if touches_generated else None

    functions: list[str] = []
    touched: list[list[str]] = []          # [path, function] pairs, for same-function overlap
    if patches is not None:
        for f in files:
            if rules.classify(f["path"]) == "GENERATED" or is_test(f["path"], rules):
                continue
            for fn in functions_in_patch(patches.get(f["path"]) or ""):
                touched.append([f["path"], fn])
                if fn not in functions:
                    functions.append(fn)

    adds = sum(f.get("additions") or 0 for f in files)
    dels = sum(f.get("deletions") or 0 for f in files)
    top_packages = [p for p, _ in sorted(packages.items(), key=lambda kv: (-kv[1], kv[0]))][:3]
    profile: dict[str, Any] = {
        "lines": f"+{adds}/-{dels}",
        "files": len(files),
        "by_class": {k: v for k, v in sorted(by_class.items()) if k != "STANDARD" or len(by_class) == 1},
        "packages": top_packages,
        "tests": "added or changed" if tests else "none",
        "code_without_tests": code > 0 and not tests,
        "docs": bool(docs),
        "api_changed": api_changed,
        "generated": generated,
        **({"truncated": "the file list stops at 100 files"} if record.get("files_truncated") else {}),
    }
    if patches is not None:
        profile["functions"] = functions[:FUNCTIONS_SHOWN]
        if len(functions) > FUNCTIONS_SHOWN:
            profile["functions_more"] = len(functions) - FUNCTIONS_SHOWN
        profile["touched"] = touched
    profile["summary"] = _summary(profile, code)
    return profile


def _summary(p: dict, code_lines: int) -> str:
    parts = [p["lines"]]
    for cls in ("API_SURFACE", "CRITICAL", "GENERATED"):
        if cls in p["by_class"]:
            parts.append(f"{cls} {p['by_class'][cls]['lines']} lines")
    if p["packages"]:
        parts.append(", ".join(p["packages"][:2]))
    if p.get("functions"):
        more = f" +{len(p['functions']) - 3 + p.get('functions_more', 0)}" if len(p["functions"]) > 3 else ""
        parts.append("touches " + ", ".join(p["functions"][:3]) + more)
    if p["code_without_tests"] and code_lines >= 20:
        parts.append("no tests")
    if p["generated"] and p["generated"] != "in sync":
        parts.append(p["generated"])
    return " · ".join(parts)


def same_function_overlaps(profiles: dict[int, dict]) -> list[dict]:
    """Pairs of PRs whose patches touch the same function in the same file: a stronger review-together signal than
    the same file, and the likeliest real conflict short of running a merge."""
    seen: dict[tuple[str, str], list[int]] = {}
    for n, p in sorted(profiles.items()):
        for path, fn in p.get("touched") or []:
            prs = seen.setdefault((path, fn), [])
            if n not in prs:
                prs.append(n)
    pairs: dict[tuple[int, int], list[str]] = {}
    for (path, fn), prs in seen.items():
        for i, a in enumerate(prs):
            for b in prs[i + 1:]:
                pairs.setdefault((a, b), []).append(f"{fn} in {path}")
    return [{"prs": [a, b], "functions": fns[:3]} for (a, b), fns in sorted(pairs.items())]
