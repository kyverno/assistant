"""The organisation pack: everything the plugins know about one organisation comes from its org.yaml
(the `maintainer:` section), never from code. A repository with no pack still works; it just has no file
classes, no gate and no bot rules, and the tools say so instead of guessing."""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class PackError(RuntimeError):
    pass


_FILE_CLASSES = ("generated", "generated_exceptions", "api_surface", "critical", "test_only")
_KNOWN: dict[str, Optional[tuple[str, ...]]] = {
    "approval_bots": None,
    "branch_gates": ("name", "status_context", "failure_issue_label", "branch_marker", "bypass_label"),
    "file_classes": _FILE_CLASSES,
    "generated_inputs": ("paths", "dependency_modules"),
    "dependency_bots": ("codegen_module_globs", "codegen_kind_label"),
    "milestones": ("title_format", "open_milestone_label", "issue_urgency_labels"),
}

# Every top-level key a pack may have: `maintainer` is the maintainer assistant's; the rest are shared or the contributor
# assistant's (profiles/contributor/plugins/contributor reads them). A key in neither profile is a typo, so it is an error.
_ROOT_KEYS = ("name", "repositories", "agent_docs", "instructions_dir", "codeowners", "labels", "readiness", "entry_labels",
              "reserved_labels", "review_bots", "ladder", "noise_paths", "test_files", "maintainer")

# The shared `readiness:` section: the maintainer reads three of these, the contributor also reads checks_file.
_READINESS_KEYS = ("ready_labels", "attention_labels", "infra_checks", "checks_file")

# Highest tier first: a file that matches several is reported as the first.
_TIERS = (("generated", "GENERATED"), ("api_surface", "API_SURFACE"), ("critical", "ADMISSION_CRITICAL"),
          ("test_only", "TEST_ONLY"))


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


# The pack baked into the image. The sandbox policy keeps /opt/kyverno read-only, so the agent cannot rewrite it, unlike
# the copy `hermes profile install` puts in Hermes' writable home. It is read first, but only for the organisations its
# `repositories:` list names (a pack with no such list applies everywhere, as in an image you built for your own
# organisation); the profile copy is for host installs, where nothing is baked.
IMAGE_PACK = Path("/opt/kyverno/profiles/maintainer/org/org.yaml")

# An organisation publishes its own pack as these two files at the root of its `.github` repository, so the published
# image works for every organisation without a clone or a build.
REMOTE_PACK = "assistant-org.yaml"
REMOTE_FACTS = "assistant-org-facts.md"
_REMOTE_TTL = 600           # seconds a fetched pack is reused before GitHub is asked again
_REMOTE_MAX = 256 * 1024    # a pack is a few KB; refuse anything absurd


def owner_of(repo: Optional[str] = None) -> Optional[str]:
    repo = repo or os.environ.get("ASSISTANT_REPO") or ""
    owner = repo.split("/", 1)[0].lower() if "/" in repo else ""
    return owner or None


def applies_to(path: Path, owner: Optional[str]) -> bool:
    """A pack that lists repositories applies only to their owners; one without the list applies to any."""
    if owner is None:
        return True
    try:
        import yaml

        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return True              # a broken pack is reported by load(), not hidden here
    repos = data.get("repositories") if isinstance(data, dict) else None
    if not repos:
        return True
    return any(isinstance(r, dict) and str(r.get("repo", "")).split("/", 1)[0].lower() == owner for r in repos)


def candidate_paths(owner: Optional[str] = None) -> list[Path]:
    paths = [IMAGE_PACK] if not IMAGE_PACK.is_file() or applies_to(IMAGE_PACK, owner) else []
    if os.environ.get("ASSISTANT_ORG_FILE"):
        paths.append(Path(os.environ["ASSISTANT_ORG_FILE"]))
    paths.append(_home() / "org" / "org.yaml")
    return paths


_remote_seen: dict[str, float] = {}


def _get(url: str) -> Optional[bytes]:
    """The body, or None for a 404. Anything else that goes wrong is a PackError: a pack that exists but cannot be read
    must not quietly turn into 'no pack', because that is how a gate gets missed."""
    import urllib.error
    import urllib.request

    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": "assistant-pack"}
    if os.environ.get("REPO_GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['REPO_GITHUB_TOKEN']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as resp:
            body = resp.read(_REMOTE_MAX + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise PackError(f"could not read {url}: HTTP {exc.code}") from exc
    except OSError as exc:
        raise PackError(f"could not read {url}: {exc}") from exc
    if len(body) > _REMOTE_MAX:
        raise PackError(f"{url} is larger than {_REMOTE_MAX} bytes")
    return body


def remote_path(owner: Optional[str]) -> Optional[Path]:
    """The organisation's own pack from `<owner>/.github`, written under Hermes' home, or None when it publishes none.
    A fetch that fails falls back to the last good copy; with none, it is an error."""
    if not owner or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", owner):
        return None
    dest = _home() / "org-cache" / owner
    target = dest / "org.yaml"
    if time.time() - _remote_seen.get(owner, 0) < _REMOTE_TTL:
        return target if target.is_file() else None
    base = f"https://api.github.com/repos/{owner}/.github/contents/"
    try:
        body = _get(base + REMOTE_PACK)
        facts = _get(base + REMOTE_FACTS) if body is not None else None
    except PackError:
        if target.is_file():
            return target
        raise
    _remote_seen[owner] = time.time()
    if body is None:
        shutil.rmtree(dest, ignore_errors=True)
        return None
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "org.yaml").write_bytes(body)
    if facts is not None:
        (dest / "facts.md").write_bytes(facts)
    else:
        (dest / "facts.md").unlink(missing_ok=True)
    return target


# -- globs ---------------------------------------------------------------------------------

def _glob_to_regex(glob: str) -> str:
    i, out = 0, ""
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i:i + 2] == "**":
                i += 2
                if glob[i:i + 1] == "/":
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
    """`**` crosses directories, `*` does not. A pattern with no "/" names a file and matches it at any depth."""
    if "/" not in glob:
        path = path.rsplit("/", 1)[-1]
    return re.fullmatch(_glob_to_regex(glob), path) is not None


def _any(path: str, globs: tuple[str, ...]) -> bool:
    return any(glob_match(g, path) for g in globs)


# -- the rules -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Gate:
    name: str
    status_context: str
    failure_issue_label: str
    branch_marker: Optional[re.Pattern]
    bypass_label: str


@dataclass(frozen=True)
class Rules:
    source: Optional[str] = None
    approval_bots: tuple[str, ...] = ()
    gates: tuple[Gate, ...] = ()
    generated: tuple[str, ...] = ()
    generated_exceptions: tuple[str, ...] = ()
    api_surface: tuple[str, ...] = ()
    critical: tuple[str, ...] = ()
    test_only: tuple[str, ...] = ()
    input_paths: tuple[str, ...] = ()
    input_modules: tuple[str, ...] = ()
    codegen_module_globs: tuple[str, ...] = ()
    codegen_kind_label: Optional[str] = None
    milestone_title_format: Optional[str] = None
    open_milestone_label: Optional[str] = None
    issue_urgency_labels: tuple[str, ...] = ()
    # Shared with the contributor (top-level keys of the same file); the maintainer only reads them.
    ready_labels: tuple[str, ...] = ()
    attention_labels: tuple[str, ...] = ()
    infra_checks: tuple[str, ...] = ()
    review_bots: tuple[str, ...] = ()
    agent_docs: tuple[str, ...] = ()
    codeowners: Optional[str] = None
    facts: str = ""

    def summary(self) -> dict:
        """What the agent is told about the pack: the rules in plain data, never the regexes or object internals."""
        return {
            "pack": self.source or "none",
            "approval_bots": list(self.approval_bots),
            "branch_gates": [
                {"name": g.name, "status_context": g.status_context, "failure_issue_label": g.failure_issue_label,
                 "marks_branch": bool(g.branch_marker), "bypass_label": g.bypass_label}
                for g in self.gates
            ],
            "file_classes": {k: list(getattr(self, k)) for k in _FILE_CLASSES},
            "generated_inputs": {"paths": list(self.input_paths), "dependency_modules": list(self.input_modules)},
            "dependency_bots": {"codegen_module_globs": list(self.codegen_module_globs),
                                "codegen_kind_label": self.codegen_kind_label},
            "milestones": {"title_format": self.milestone_title_format, "open_milestone_label": self.open_milestone_label,
                           "issue_urgency_labels": list(self.issue_urgency_labels)},
            "readiness": {"ready_labels": list(self.ready_labels), "attention_labels": list(self.attention_labels),
                          "infra_checks": list(self.infra_checks)},
            "review_bots": list(self.review_bots),
            "agent_docs": list(self.agent_docs),
            "codeowners": self.codeowners,
        }

    def classify(self, path: str) -> str:
        """'GENERATED' | 'API_SURFACE' | 'ADMISSION_CRITICAL' | 'TEST_ONLY' | 'STANDARD'."""
        if not _any(path, self.generated_exceptions) and _any(path, self.generated):
            return "GENERATED"
        for key, tier in _TIERS[1:]:
            if _any(path, getattr(self, key)):
                return tier
        return "STANDARD"

    def is_test_only(self, path: str) -> bool:
        return _any(path, self.test_only)

    def is_generated_input_path(self, path: str) -> bool:
        return _any(path, self.input_paths)

    @property
    def gate(self) -> Optional[Gate]:
        """The first configured gate. A caller passes one list of blocked branches, so one gate is all it can express."""
        return self.gates[0] if self.gates else None


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PackError(f"{where}: expected a list of strings")
    return tuple(value)


def _text(value: Any, where: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PackError(f"{where}: expected text")
    return value


def _check_keys(section: Any, allowed: tuple[str, ...], where: str) -> dict:
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise PackError(f"{where}: expected a mapping")
    unknown = sorted(set(section) - set(allowed))
    if unknown:
        raise PackError(f"{where}: unknown keys {unknown}")
    return section


def parse(data: Any, source: Optional[str] = None, facts: str = "") -> Rules:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise PackError(f"the file must be a mapping of keys, not a {type(data).__name__}")
    _check_keys(data, _ROOT_KEYS, "the file")
    m = _check_keys(data.get("maintainer"), tuple(_KNOWN), "maintainer")
    fc = _check_keys(m.get("file_classes"), _FILE_CLASSES, "maintainer.file_classes")
    gi = _check_keys(m.get("generated_inputs"), _KNOWN["generated_inputs"], "maintainer.generated_inputs")
    db = _check_keys(m.get("dependency_bots"), _KNOWN["dependency_bots"], "maintainer.dependency_bots")
    ms = _check_keys(m.get("milestones"), _KNOWN["milestones"], "maintainer.milestones")

    readiness = _check_keys(data.get("readiness"), _READINESS_KEYS, "readiness")
    _text(readiness.get("checks_file"), "readiness.checks_file")
    raw_gates = m.get("branch_gates")
    if raw_gates is None:
        raw_gates = []
    if not isinstance(raw_gates, list):
        raise PackError("maintainer.branch_gates: expected a list")
    gates = []
    for i, g in enumerate(raw_gates):
        g = _check_keys(g, _KNOWN["branch_gates"], f"maintainer.branch_gates[{i}]")
        for required in ("name", "failure_issue_label", "bypass_label"):
            if not g.get(required):
                raise PackError(f"maintainer.branch_gates[{i}]: {required} is required")
            _text(g[required], f"maintainer.branch_gates[{i}].{required}")
        _text(g.get("status_context"), f"maintainer.branch_gates[{i}].status_context")
        _text(g.get("branch_marker"), f"maintainer.branch_gates[{i}].branch_marker")
        try:
            marker = re.compile(g["branch_marker"]) if g.get("branch_marker") else None
        except re.error as exc:
            raise PackError(f"maintainer.branch_gates[{i}].branch_marker: {exc}") from exc
        if marker and "branch" not in marker.groupindex:
            raise PackError(f"maintainer.branch_gates[{i}].branch_marker: needs a (?P<branch>...) group")
        gates.append(Gate(g["name"], g.get("status_context") or "", g["failure_issue_label"], marker, g["bypass_label"]))

    return Rules(
        source=source,
        approval_bots=_strings(m.get("approval_bots"), "maintainer.approval_bots"),
        gates=tuple(gates),
        **{k: _strings(fc.get(k), f"maintainer.file_classes.{k}") for k in _FILE_CLASSES},
        input_paths=_strings(gi.get("paths"), "maintainer.generated_inputs.paths"),
        input_modules=_strings(gi.get("dependency_modules"), "maintainer.generated_inputs.dependency_modules"),
        codegen_module_globs=_strings(db.get("codegen_module_globs"), "maintainer.dependency_bots.codegen_module_globs"),
        codegen_kind_label=_text(db.get("codegen_kind_label"), "maintainer.dependency_bots.codegen_kind_label"),
        milestone_title_format=_text(ms.get("title_format"), "maintainer.milestones.title_format"),
        open_milestone_label=_text(ms.get("open_milestone_label"), "maintainer.milestones.open_milestone_label"),
        issue_urgency_labels=_strings(ms.get("issue_urgency_labels"), "maintainer.milestones.issue_urgency_labels"),
        ready_labels=_strings(readiness.get("ready_labels"), "readiness.ready_labels"),
        attention_labels=_strings(readiness.get("attention_labels"), "readiness.attention_labels"),
        infra_checks=_strings(readiness.get("infra_checks"), "readiness.infra_checks"),
        review_bots=_strings(data.get("review_bots"), "review_bots"),
        agent_docs=_strings(data.get("agent_docs"), "agent_docs"),
        codeowners=_text(data.get("codeowners"), "codeowners"),
        facts=facts,
    )


_cache: dict[tuple, Rules] = {}


def _read(p: Path) -> Rules:
    key = (str(p), p.stat().st_mtime, (p.parent / "facts.md").stat().st_mtime if (p.parent / "facts.md").is_file() else 0)
    if key not in _cache:
        try:
            import yaml
        except ImportError as exc:
            raise PackError("PyYAML is not available to read the organisation pack") from exc
        try:
            facts_file = p.parent / "facts.md"
            facts = facts_file.read_text() if facts_file.is_file() else ""
            _cache[key] = parse(yaml.safe_load(p.read_text()), str(p), facts)
        except yaml.YAMLError as exc:
            raise PackError(f"{p}: {exc}") from exc
        except PackError as exc:
            raise PackError(f"{p}: {exc}") from exc
    return _cache[key]


def load(path: Optional[str] = None, repo: Optional[str] = None) -> Rules:
    """The rules for `repo` (default: ASSISTANT_REPO): the pack baked into the image if it covers that owner, else the
    owner's own `.github/assistant-org.yaml`, else the home copy; empty rules when there is none. A pack that exists but
    is wrong, or that cannot be read, is an error."""
    if path:
        return _read(Path(path)) if Path(path).is_file() else Rules()
    owner = owner_of(repo)
    paths = candidate_paths(owner)
    # The baked pack (when it applies) and an explicit file come first; the organisation's published pack is next.
    ordered = [p for p in paths if p == IMAGE_PACK or str(p) == os.environ.get("ASSISTANT_ORG_FILE")]
    for p in ordered:
        if p.is_file():
            return _read(p)
    remote = remote_path(owner)
    if remote is not None:
        return _read(remote)
    for p in paths:
        if p not in ordered and p.is_file():
            return _read(p)
    return Rules()
