"""What the organisation file and the repository's own files say: org.yaml loader, labeler globs,
CODEOWNERS, Markdown headings. Computed by code so the model never guesses an area or an owner."""

from __future__ import annotations

import base64
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import github
from .github import hermes_home


# -- organisation file ------------------------------------------------------------------
REQUIRED = ("name", "agent_docs", "labels", "readiness", "review_bots", "ladder")


class OrgFileError(RuntimeError):
    pass


class OrgFileUnavailable(OrgFileError):
    """The pack could not be fetched right now (network, GitHub 5xx, rate limit). Only this may fall back to the last good
    copy: a 403 or an oversized file means the pack as it is now is unreadable, which is an error."""


def _transient(message: str) -> bool:
    return message.startswith("could not reach GitHub") or "rate limit hit" in message or message.startswith("GitHub 5")


# The pack baked into the image: read-only to the agent (the sandbox policy keeps /opt/kyverno read-only), unlike the copy
# in Hermes' writable home. It is read first, but only for the organisations its `repositories:` list names (one without
# the list applies everywhere, as in an image you built for your own organisation); the home copy is for installs where
# nothing is baked.
IMAGE_PACK = Path("/opt/kyverno/profiles/contributor/org/org.yaml")

# An organisation publishes its own pack as this file (and optionally the facts file) at the root of its `.github`
# repository, so the published image works for every organisation without a clone or a build.
REMOTE_PACK = "assistant-org.yaml"
REMOTE_FACTS = "assistant-org-facts.md"
_REMOTE_TTL = 600
_remote_seen: Dict[str, float] = {}


def owner_of(repo: Optional[str] = None) -> Optional[str]:
    repo = repo or github.secret("ASSISTANT_REPO") or ""
    owner = repo.split("/", 1)[0].lower() if "/" in repo else ""
    return owner or None


def applies_to(path: Path, owner: Optional[str]) -> bool:
    if owner is None:
        return True
    import yaml

    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return True              # a broken pack is reported by load_org, not hidden here
    repos = data.get("repositories") if isinstance(data, dict) else None
    if not repos:
        return True
    return any(isinstance(r, dict) and str(r.get("repo", "")).split("/", 1)[0].lower() == owner for r in repos)


def candidate_paths(owner: Optional[str] = None) -> list:
    paths = [IMAGE_PACK] if not IMAGE_PACK.is_file() or applies_to(IMAGE_PACK, owner) else []
    explicit = os.environ.get("ASSISTANT_ORG_FILE")
    if explicit:
        paths.append(Path(explicit))
    paths.append(hermes_home() / "org" / "org.yaml")
    return paths


def _remote_file(client: "github.Client", owner: str, name: str) -> Optional[bytes]:
    try:
        item = client.rest(f"repos/{owner}/.github/contents/{name}")
    except github.GitHubError as exc:
        if str(exc).startswith("GitHub 404"):
            return None
        cls = OrgFileUnavailable if _transient(str(exc)) else OrgFileError
        raise cls(f"could not read {owner}/.github/{name}: {exc}") from exc
    if not isinstance(item, dict) or item.get("encoding") != "base64" or item.get("size", 0) > 256 * 1024:
        raise OrgFileError(f"{owner}/.github/{name} is not a file of a usable size")
    return base64.b64decode(item.get("content", ""))


def remote_path(owner: Optional[str], client: Optional["github.Client"] = None) -> Optional[Path]:
    """The organisation's own pack from `<owner>/.github`, written under Hermes' home, or None when it publishes none.
    A fetch that fails falls back to the last good copy; with none, it is an error rather than 'no pack'."""
    if not owner or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", owner):
        return None
    dest = hermes_home() / "org-cache" / owner
    target = dest / "org.yaml"
    if time.time() - _remote_seen.get(owner, 0) < _REMOTE_TTL:
        return target if target.is_file() else None
    client = client or github.Client()
    try:
        body = _remote_file(client, owner, REMOTE_PACK)
        facts = _remote_file(client, owner, REMOTE_FACTS) if body is not None else None
    except OrgFileUnavailable:
        if target.is_file():
            return target
        raise
    _remote_seen[owner] = time.time()
    if body is None:
        shutil.rmtree(dest, ignore_errors=True)
        return None
    dest.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    if facts is not None:
        (dest / "facts.md").write_bytes(facts)
    else:
        (dest / "facts.md").unlink(missing_ok=True)
    return target


def _read_org(p: Path) -> Dict[str, Any]:
    import yaml

    data = yaml.safe_load(p.read_text()) or {}
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise OrgFileError(f"{p}: missing keys {missing}")
    return data


def load_org(path: Optional[str] = None, repo: Optional[str] = None) -> Dict[str, Any]:
    """The organisation file for `repo` (default: ASSISTANT_REPO): the pack baked into the image if it covers that owner,
    else the owner's `.github/assistant-org.yaml`, else the home copy."""
    if path:
        if Path(path).is_file():
            return _read_org(Path(path))
        raise OrgFileError(f"organisation file not found: {path}")
    owner = owner_of(repo)
    paths = candidate_paths(owner)
    first = [p for p in paths if p == IMAGE_PACK or str(p) == os.environ.get("ASSISTANT_ORG_FILE")]
    for p in first:
        if p.is_file():
            return _read_org(p)
    remote = remote_path(owner)
    if remote is not None:
        return _read_org(remote)
    for p in paths:
        if p not in first and p.is_file():
            return _read_org(p)
    raise OrgFileError(
        f"no organisation pack for {owner or 'this repository'}: it publishes none at {owner or '<owner>'}/.github/{REMOTE_PACK} "
        "(see docs/organisation-pack.md)")


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
