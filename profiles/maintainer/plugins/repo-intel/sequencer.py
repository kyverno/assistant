"""Deterministic PR merge-sequencing core: a graph builder, not a decision maker.

Pure computation over already-fetched PR metadata — no GitHub/Slack calls, no state.

Design: the sequencer only owns structural facts — hard dependencies, cycles, a topological
layering into tiers, and a few mechanically-derived annotations. Ordering *within* a tier
(two PRs with no real dependency between them) is a genuine judgment call — Slack context,
what the maintainer said last session, what they're trying to get done today — and belongs to
the agent, not a weighted formula pretending to resolve it. See skills/pr-queue/SKILL.md's
Procedure for how tiers and annotations get used.

Four hard-edge types, all mechanically derivable from already-fetched metadata — no caller-
supplied hints needed:

1. Stacked branches (`base_branch` is another candidate's `head_branch`).
2. Generated-file input-before-output (a candidate touching an `api/**/*_types.go` path or a
   `github.com/kyverno/api` bump must precede one touching the regenerated output it feeds).
3. Explicit body reference — the PR's own body says "Depends on #N" / "Blocked by #N" /
   "Requires #N" / "Stacked on #N" naming another candidate.
4. Closing-issue conflict — two candidates closing the *same* issue aren't ordered against
   each other at all; only one can actually close it, so this goes to `unresolved[]` for a
   human, not a guessed edge.

Interface-direction and dependency-usage edges (approximating "which PR's diff implies which
other PR must come first" from a search rather than a real call graph) are deliberately not
attempted here — see docs/v3-plan.md's gopls-integration entry for the real version of that.

File-risk classification patterns below are a code mirror of
skills/kyverno-context/SKILL.md's file-risk classification reference. Keep both in sync
manually if Kyverno's generated-path list changes.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# --- file classification ---------------------------------------------------------------------

GENERATED_GLOB_PATTERNS = [
    "zz_generated.deepcopy.go",
    "zz_generated.register.go",
    "pkg/client/*",
    "pkg/clients/*.generated.go",
    "pkg/clients/*/interface.generated.go",
    "config/crds/*.yaml",
    "cmd/cli/kubectl-kyverno/config/crds/*",
    "cmd/cli/kubectl-kyverno/data/crds/*",
    "charts/kyverno/charts/crds/templates/*",
    "charts/*/README.md",
    "docs/user/crd/*",
    "pkg/config/mocks/mock_config.go",
    "config/install-latest-testing.yaml",
]

# The one documented hand-written exception inside an otherwise-generated directory.
GENERATED_EXCEPTIONS = {"pkg/clients/dclient/client.go"}

API_SURFACE_GLOB_PATTERNS = ["api/kyverno/*", "api/policyreport/*", "api/reports/*"]
ADMISSION_CRITICAL_GLOB_PATTERNS = ["pkg/engine/*", "pkg/webhooks/*", "pkg/cel/*"]
TEST_ONLY_GLOB_PATTERNS = ["*_test.go", "test/*", "*/testdata/*", "*/_testdata/*"]

# Generated-file *inputs* — distinct from the API_SURFACE display tier above: this drives
# edge-building (input-before-output), not what gets shown to the maintainer.
GENERATED_INPUT_GLOB_PATTERNS = ["api/*_types.go"]
GENERATED_INPUT_DEPENDENCY_MODULE = "github.com/kyverno/api"

# The repo these candidates come from. A reference naming another repo ("depends on kyverno/api#72")
# is not about a candidate with that number.
REPO = "kyverno/kyverno"

# Covers real phrasing variants seen in the wild, not just the canonical form: "Parent:
# #17701", "Stacks on top of #17695", "depends on kyverno/kyverno#17720", "Blocked by #N".
_CROSS_REF_PATTERN = re.compile(
    r"\b(?:depends on|blocked by|requires|stack(?:s|ed)?\s+on(?:\s+top\s+of)?|parent)\b"
    r"\s*:?\s*(?:([\w.-]+/[\w.-]+))?#(\d+)",
    re.IGNORECASE,
)


def _norm(pattern: str) -> str:
    """fnmatch's '*' already matches path separators, so a doc-style '**' collapses to '*'."""
    return pattern.replace("**", "*")


def _match_any(path: str, patterns: list[str]) -> bool:
    """A pattern with no '/' matches the file name at any depth, the way the source rules it mirrors
    (.claude/settings.json) read a bare `zz_generated.deepcopy.go`; fnmatch alone would only match
    it at the repo root, where no such file exists."""
    base = path.rsplit("/", 1)[-1]
    for pat in patterns:
        pat = _norm(pat)
        if fnmatch.fnmatch(path, pat) or ("/" not in pat and fnmatch.fnmatch(base, pat)):
            return True
    return False


def classify_file(path: str) -> str:
    """'GENERATED' | 'API_SURFACE' | 'ADMISSION_CRITICAL' | 'TEST_ONLY' | 'STANDARD', checked
    in that priority order — a file can match more than one tier's glob (e.g. a test file
    under pkg/engine/), and the higher-priority tier wins."""
    if path not in GENERATED_EXCEPTIONS and _match_any(path, GENERATED_GLOB_PATTERNS):
        return "GENERATED"
    if _match_any(path, API_SURFACE_GLOB_PATTERNS):
        return "API_SURFACE"
    if _match_any(path, ADMISSION_CRITICAL_GLOB_PATTERNS):
        return "ADMISSION_CRITICAL"
    if _match_any(path, TEST_ONLY_GLOB_PATTERNS):
        return "TEST_ONLY"
    return "STANDARD"


_TIER_RANK = {"GENERATED": 0, "API_SURFACE": 1, "ADMISSION_CRITICAL": 2, "TEST_ONLY": 3, "STANDARD": 4}


# --- PR model ---------------------------------------------------------------------------

@dataclass
class PR:
    number: int
    changed_files: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    base_branch: str = ""
    head_branch: str = ""
    body: str = ""
    closing_issues: list[int] = field(default_factory=list)
    dependency_bumps: list[str] = field(default_factory=list)
    files_truncated: bool = False

    @property
    def size(self) -> int:
        return len(self.changed_files)

    def is_generated_input(self) -> bool:
        return (GENERATED_INPUT_DEPENDENCY_MODULE in self.dependency_bumps
                or any(_match_any(f, GENERATED_INPUT_GLOB_PATTERNS) for f in self.changed_files))

    def is_generated_output_toucher(self) -> bool:
        return any(classify_file(f) == "GENERATED" for f in self.changed_files)

    def has_bypass_label(self) -> bool:
        return "e2e-gate-bypass" in self.labels

    def file_classification(self) -> str:
        if not self.changed_files:
            return "STANDARD"
        return min((classify_file(f) for f in self.changed_files), key=lambda t: _TIER_RANK[t])


def pr_from_dict(d: dict) -> PR:
    raw_closing = d.get("closing_issues")
    if not isinstance(raw_closing, (list, tuple, set)):
        raw_closing = [raw_closing] if raw_closing is not None else []

    closing_issues: list[int] = []
    seen_issues: set[int] = set()
    for item in raw_closing:
        if isinstance(item, bool):
            continue
        num = None
        if isinstance(item, dict):
            val = item.get("number")
            if not isinstance(val, bool) and val is not None:
                try:
                    num = int(val)
                except (ValueError, TypeError):
                    pass
        elif item is not None:
            try:
                num = int(item)
            except (ValueError, TypeError):
                pass

        if num is not None and num > 0 and num not in seen_issues:
            seen_issues.add(num)
            closing_issues.append(num)

    return PR(
        number=int(d["number"]),
        changed_files=list(d.get("changed_files") or []),
        labels=list(d.get("labels") or []),
        base_branch=str(d.get("base_branch") or ""),
        head_branch=str(d.get("head_branch") or ""),
        body=str(d.get("body") or ""),
        closing_issues=closing_issues,
        files_truncated=bool(d.get("files_truncated")),
        dependency_bumps=list(d.get("dependency_bumps") or []),
    )


# --- hard-edge graph ----------------------------------------------------------------------

def build_hard_edges(prs: list[PR], repo_default_branch: str) -> tuple[list[tuple[int, int, str]], list[dict]]:
    """Returns (edges, unresolved) — an edge is (before, after, kind); unresolved covers pairs
    with no derivable direction (both inputs to the same generated output) or no direction at
    all (a closing-issue conflict)."""
    edges: list[tuple[int, int, str]] = []
    unresolved: list[dict] = []
    by_number = {p.number: p for p in prs}
    by_head = {p.head_branch: p for p in prs if p.head_branch}

    for p in prs:
        # 1. Stacked.
        if p.base_branch and p.base_branch != repo_default_branch and p.base_branch in by_head:
            base_pr = by_head[p.base_branch]
            if base_pr.number != p.number:
                edges.append((base_pr.number, p.number, "stacked"))

        # 3. Explicit body reference.
        for match in _CROSS_REF_PATTERN.finditer(p.body):
            if match.group(1) and match.group(1).lower() != REPO:
                continue
            ref = int(match.group(2))
            if ref in by_number and ref != p.number:
                edges.append((ref, p.number, "explicit reference"))

    # 2. Generated-file input-before-output.
    input_only = [p for p in prs if p.is_generated_input() and not p.is_generated_output_toucher()]
    output_touchers = [p for p in prs if p.is_generated_output_toucher()]
    both_input = [p for p in prs if p.is_generated_input() and p.is_generated_output_toucher()]

    for a in input_only:
        for b in output_touchers:
            if a.number != b.number:
                edges.append((a.number, b.number, "generated-file"))

    all_inputs = input_only + both_input
    for i, a in enumerate(all_inputs):
        for b in all_inputs[i + 1:]:
            unresolved.append({
                "prs": [a.number, b.number],
                "why": "both touch a generated-file input (or a github.com/kyverno/api bump) "
                       "feeding the same regenerated output, with no derivable order between them",
            })

    # 4. Closing-issue conflict.
    by_issue: dict[int, list[int]] = {}
    for p in prs:
        for issue in p.closing_issues:
            by_issue.setdefault(issue, []).append(p.number)
    for issue, numbers in by_issue.items():
        if len(numbers) > 1:
            for i, a in enumerate(numbers):
                for b in numbers[i + 1:]:
                    unresolved.append({"prs": [a, b], "why": f"both close #{issue}"})

    return edges, unresolved


# --- cycle detection (Kahn's algorithm) -----------------------------------------------------

def find_cycles_and_acyclic_order_candidates(
    numbers: list[int], edges: list[tuple[int, int, str]]
) -> tuple[list[int], list[int]]:
    """Returns (acyclic_numbers, cyclic_numbers). Numbers left with unresolved in-degree after
    Kahn's algorithm drains everything it can are part of a cycle."""
    indeg = {n: 0 for n in numbers}
    succ: dict[int, list[int]] = {n: [] for n in numbers}
    for a, b, _kind in edges:
        if a in succ and b in indeg:
            succ[a].append(b)
            indeg[b] += 1

    queue = [n for n in numbers if indeg[n] == 0]
    seen: list[int] = []
    indeg = dict(indeg)
    while queue:
        n = queue.pop(0)
        seen.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    seen_set = set(seen)
    cyclic = [n for n in numbers if n not in seen_set]
    return seen, cyclic


# --- topological layering into tiers --------------------------------------------------------

def layer_into_tiers(numbers: list[int], edges: list[tuple[int, int, str]]) -> list[list[int]]:
    """BFS-by-level topological sort: tier k holds every PR whose longest dependency chain
    from any source is exactly k. Within a tier there is no hard edge between any two
    members — the caller orders them, this function doesn't."""
    indeg = {n: 0 for n in numbers}
    succ: dict[int, list[int]] = {n: [] for n in numbers}
    for a, b, _kind in edges:
        if a in indeg and b in indeg:
            succ[a].append(b)
            indeg[b] += 1

    tiers: list[list[int]] = []
    processed: set[int] = set()
    layer = sorted(n for n in numbers if indeg[n] == 0)
    while layer:
        tiers.append(layer)
        processed.update(layer)
        next_layer: set[int] = set()
        for n in layer:
            for m in succ[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    next_layer.add(m)
        layer = sorted(next_layer - processed)
    return tiers


# --- annotations ---------------------------------------------------------------------------

def gate_blocked(p: PR, blocking_branches: set[str], blocks_all_branches: bool) -> bool:
    """True when GitHub's own 'E2E Gate' status would currently be red on this PR: an open
    e2e-failure issue's branch marker matches this PR's base (or an issue has no marker at
    all, which blocks every branch) — unconditionally, regardless of which files the PR
    touches. The only escape is the PR carrying e2e-gate-bypass itself."""
    if p.has_bypass_label():
        return False
    return blocks_all_branches or p.base_branch in blocking_branches


def find_file_overlaps(prs: list[PR], edges: list[tuple[int, int, str]]) -> list[dict]:
    """Pairs of candidates touching the exact same file, no existing hard edge between them —
    a real rebase-conflict *candidate*. Touching the same file doesn't mean the same lines;
    `fetch_file_diff_overlap` checks that. Each path is tagged with its file_classification so
    the same-file-but-TEST_ONLY case reads differently from the same-file-but-GENERATED one."""
    edge_pairs = {frozenset((a, b)) for a, b, _kind in edges}
    files_by_pr = {p.number: set(p.changed_files) for p in prs}
    overlaps: list[dict] = []
    for i, a in enumerate(prs):
        for b in prs[i + 1:]:
            if frozenset((a.number, b.number)) in edge_pairs:
                continue
            shared = sorted(files_by_pr[a.number] & files_by_pr[b.number])
            if shared:
                overlaps.append({
                    "prs": [a.number, b.number],
                    "files": [{"path": f, "file_classification": classify_file(f)} for f in shared],
                })
    return overlaps


def find_package_overlaps(prs: list[PR], edges: list[tuple[int, int, str]], file_overlap_pairs: set[frozenset]) -> list[dict]:
    """Pairs of candidates touching the same directory but *not* the same file — weaker than
    `find_file_overlaps`: different files can't git-conflict, this is only "review together,
    might interact" territory. Excludes pairs already reported as a file overlap, so the two
    annotations don't double up on the same pair.

    Every file counts except generated ones and tests: regenerated output and test files share
    directories without the code interacting. Engine, webhook, CEL and API code does count —
    two PRs in different files of the same package are exactly the pair worth reviewing together."""
    edge_pairs = {frozenset((a, b)) for a, b, _kind in edges}
    dirs_by_pr = {
        p.number: {os.path.dirname(f) for f in p.changed_files
                   if classify_file(f) != "GENERATED" and not _match_any(f, TEST_ONLY_GLOB_PATTERNS)}
        for p in prs
    }
    overlaps: list[dict] = []
    for i, a in enumerate(prs):
        for b in prs[i + 1:]:
            pair = frozenset((a.number, b.number))
            if pair in edge_pairs or pair in file_overlap_pairs:
                continue
            shared = sorted(d for d in (dirs_by_pr[a.number] & dirs_by_pr[b.number]) if d)
            if shared:
                overlaps.append({"prs": [a.number, b.number], "paths": shared[:3]})
    return overlaps


# --- top-level entry point -------------------------------------------------------------------

def sequence(
    pr_dicts: list[dict],
    repo_default_branch: str = "main",
    blocking_issue_branches: Optional[list[Optional[str]]] = None,
) -> dict[str, Any]:
    prs = [pr_from_dict(d) for d in pr_dicts]
    numbers = [p.number for p in prs]
    by_number = {p.number: p for p in prs}

    branches = blocking_issue_branches or []
    blocking_branches = {b for b in branches if b is not None}
    blocks_all_branches = None in branches

    edges, unresolved = build_hard_edges(prs, repo_default_branch)

    acyclic, cyclic = find_cycles_and_acyclic_order_candidates(numbers, edges)
    cyclic_set = set(cyclic)
    solved_prs = [p for p in prs if p.number not in cyclic_set]
    solved_edges = [(a, b, k) for a, b, k in edges if a not in cyclic_set and b not in cyclic_set]
    solved_numbers = [p.number for p in solved_prs]

    tiers_numbers = layer_into_tiers(solved_numbers, solved_edges)
    file_overlaps = find_file_overlaps(solved_prs, solved_edges)
    file_overlap_pairs = {frozenset(o["prs"]) for o in file_overlaps}
    package_overlaps = find_package_overlaps(solved_prs, solved_edges, file_overlap_pairs)

    preds: dict[int, list[tuple[int, str]]] = {n: [] for n in solved_numbers}
    succs: dict[int, list[tuple[int, str]]] = {n: [] for n in solved_numbers}
    for a, b, kind in solved_edges:
        preds[b].append((a, kind))
        succs[a].append((b, kind))

    earlier_files: set[str] = set()
    tiers_out = []
    for tier_index, tier_numbers in enumerate(tiers_numbers, start=1):
        tier_prs_out = []
        for number in tier_numbers:
            p = by_number[number]
            hard_constraints = [f"must follow #{a} ({kind})" for a, kind in preds[number]]
            hard_constraints += [f"must precede #{b} ({kind})" for b, kind in succs[number]]
            rebase_flag = bool(set(p.changed_files) & earlier_files)
            blocked = gate_blocked(p, blocking_branches, blocks_all_branches)
            warnings = []
            if blocked:
                warnings.append("gate_blocked: an open e2e-failure issue blocks merging on this branch")
            if p.files_truncated:
                warnings.append("files_truncated: changed_files is cut off at the fetch limit, so this PR's "
                                "classification, overlaps and rebase_flag may be incomplete")
            tier_prs_out.append({
                "pr": number,
                "hard_constraints": hard_constraints,
                "rebase_flag": rebase_flag,
                "warnings": warnings,
                "gate_blocked": blocked,
                "file_classification": p.file_classification(),
            })
        earlier_files |= {f for n in tier_numbers for f in by_number[n].changed_files}
        tiers_out.append({"tier": tier_index, "prs": tier_prs_out})

    return {
        "tiers": tiers_out,
        "cycles": [cyclic] if cyclic else [],
        "unresolved": unresolved,
        "file_overlaps": file_overlaps,
        "package_overlaps": package_overlaps,
        "gate_status": {
            "blocking_branches": sorted(blocking_branches),
            "blocks_all_branches": blocks_all_branches,
        },
    }
