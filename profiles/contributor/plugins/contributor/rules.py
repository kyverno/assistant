"""What the organisation file and the repository's own files say: org.yaml loader, labeler globs,
CODEOWNERS, Markdown headings. Computed by code so the model never guesses an area or an owner."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .github import hermes_home


# -- organisation file ------------------------------------------------------------------
REQUIRED = ("name", "agent_docs", "labels", "readiness", "review_bots", "ladder")


class OrgFileError(RuntimeError):
    pass


def candidate_paths() -> list:
    
    paths = []
    if os.environ.get("KYVERNO_ORG_FILE"):
        paths.append(Path(os.environ["KYVERNO_ORG_FILE"]))
    paths.append(hermes_home() / "org" / "org.yaml")
    return paths


def load_org(path: Optional[str] = None) -> Dict[str, Any]:
    import yaml

    paths = [Path(path)] if path else candidate_paths()
    for p in paths:
        if p.is_file():
            data = yaml.safe_load(p.read_text()) or {}
            missing = [k for k in REQUIRED if k not in data]
            if missing:
                raise OrgFileError(f"{p}: missing keys {missing}")
            return data
    raise OrgFileError("organisation file not found (looked in: %s)" % ", ".join(map(str, paths)))


# -- globs, label rules, CODEOWNERS -----------------------------------------------------
def _expand_braces(pattern: str) -> List[str]:
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out: List[str] = []
    for alt in m.group(1).split(","):
        out.extend(_expand_braces(pattern[: m.start()] + alt + pattern[m.end():]))
    return out


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


def glob_match(glob: str, path: str) -> bool:
    return any(re.fullmatch(_glob_to_regex(g), path) for g in _expand_braces(glob))


# -- actions/labeler rules (kyverno .github/labels.yml) ---------------------------------

def label_rules(labels_yml: Dict[str, Any]) -> Dict[str, List[Dict[str, List[str]]]]:
    """{label: [{"any": [globs], "all": [globs]}]} from labels.yml; labels without path rules are dropped."""
    out: Dict[str, List[Dict[str, List[str]]]] = {}
    for label, spec in (labels_yml or {}).items():
        if not isinstance(spec, dict):
            continue
        rules: List[Dict[str, List[str]]] = []
        for rule in spec.get("rules") or []:
            for matcher in rule.get("changed-files") or []:
                any_globs = list(matcher.get("any-glob-to-any-file") or [])
                all_globs = list(matcher.get("all-globs-to-all-files") or [])
                if any_globs or all_globs:
                    rules.append({"any": any_globs, "all": all_globs})
        if rules:
            out[label] = rules
    return out


def labels_for_paths(rules: Dict[str, List[Dict[str, List[str]]]], paths: Iterable[str],
                     prefixes: Tuple[str, ...] = ()) -> Dict[str, List[str]]:
    """{label: [paths that triggered it]}. `prefixes` limits to labels such as `area/`."""
    paths = list(paths)
    result: Dict[str, List[str]] = {}
    for label, label_rules_ in rules.items():
        if prefixes and not label.startswith(prefixes):
            continue
        hit: List[str] = []
        for rule in label_rules_:
            if rule["any"]:
                hit += [p for p in paths if any(glob_match(g, p) for g in rule["any"])]
            if rule["all"] and paths and all(any(glob_match(g, p) for g in rule["all"]) for p in paths):
                hit += paths
        if hit:
            result[label] = sorted(set(hit))
    return result


# -- CODEOWNERS ------------------------------------------------------------------------

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


# -- files read from the repository -----------------------------------------------------
def blob_url(repo: str, sha: str, path: str, line: Optional[int] = None) -> str:
    return f"https://github.com/{repo}/blob/{sha}/{path}" + (f"#L{line}" if line else "")


def label_rules_at(client, org: Dict[str, Any], repo: str, ref: str) -> Dict[str, Any]:
    import yaml

    text = client.get_file(repo, org["labels"]["source"], ref)
    if text is None:
        return {}
    return label_rules(yaml.safe_load(text) or {})


def codeowners_at(client, org: Dict[str, Any], repo: str, ref: str) -> List[Tuple[str, List[str]]]:
    text = client.get_file(repo, org.get("codeowners", "CODEOWNERS"), ref)
    return parse_codeowners(text) if text else []


def area_labels(rules: Dict[str, Any], org: Dict[str, Any], paths: List[str]) -> Dict[str, List[str]]:
    prefixes = tuple(org["labels"].get("area_prefixes", [])) + tuple(org["labels"].get("kind_prefixes", []))
    return labels_for_paths(rules, paths, prefixes)


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def anchor(title: str, seen: Dict[str, int]) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation, spaces to hyphens, -N for repeats."""
    base = re.sub(r"[^\w\- ]", "", title.lower()).strip().replace(" ", "-")
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def headings(text: str) -> List[Dict[str, Any]]:
    out, seen, in_code = [], {}, False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else _HEADING.match(line)
        if m:
            out.append({"level": len(m.group(1)), "title": m.group(2), "anchor": anchor(m.group(2), seen), "line": i})
    return out


def sections(text: str) -> List[Dict[str, Any]]:
    """Heading sections with their body text and line span."""
    lines = text.splitlines()
    hs = headings(text)
    out = []
    for idx, h in enumerate(hs):
        end = hs[idx + 1]["line"] - 1 if idx + 1 < len(hs) else len(lines)
        out.append({**h, "end_line": end, "body": "\n".join(lines[h["line"]: end])})
    return out


def path_prefixes(path: str) -> List[str]:
    """pkg/webhooks/resource/x.go -> pkg/webhooks/resource, pkg/webhooks, pkg (two+ segments first)."""
    parts = path.split("/")[:-1]
    return ["/".join(parts[:n]) for n in range(len(parts), 0, -1)]


def relevant_sections(text: str, path: str, limit: int = 4) -> List[Dict[str, Any]]:
    """Sections of a doc that mention the path or one of its parent directories (longest first)."""
    prefixes = [p for p in path_prefixes(path) if p.count("/") >= 1] or path_prefixes(path)
    hits = []
    for sec in sections(text):
        haystack = sec["title"] + "\n" + sec["body"]
        for rank, prefix in enumerate(prefixes):
            if prefix and prefix in haystack:
                hits.append((rank, sec["line"], sec, prefix))
                break
    hits.sort(key=lambda h: (h[0], h[1]))
    return [{**{k: s[k] for k in ("title", "anchor", "line", "end_line")}, "matched": prefix,
             "body": s["body"][:1500]} for _, _, s, prefix in hits[:limit]]
