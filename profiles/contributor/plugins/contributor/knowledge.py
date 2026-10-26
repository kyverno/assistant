"""knowledge_for (what the repository says about a path) and explain_path (how a file or folder
relates to the rest of the code). Logic only; tools.py wraps these for Hermes."""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from typing import Any, Dict, List, Optional

from . import rules
from .github import doc_text, excerpt

def resolve(client, repo: str, ref: Optional[str]):
    if ref and re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref, ref
    if ref:
        return ref, client.rest(f"repos/{repo}/commits/{ref}")["sha"]
    branch, sha = client.default_branch_sha(repo)
    return branch, sha


def _text(client, repo: str, sha: str, path: str) -> Optional[str]:
    return client.get_file(repo, path, sha)          # remembered by commit in the client


def _dir(client, repo: str, sha: str, directory: str) -> List[Dict[str, Any]]:
    return client.listing(repo, directory, sha)


def _names(client, repo: str, sha: str, directory: str) -> set:
    return {e["name"] for e in _dir(client, repo, sha, directory) if e["type"] == "file"}


def prefetch(client, org: Dict[str, Any], repo: str, sha: str, paths: List[str]) -> None:
    """Everything knowledge_for reads, in one request: the label rules, CODEOWNERS, the top-level guides, every
    AGENTS.md that could apply to these paths, and the instruction files with their text."""
    files = {org["labels"]["source"], org.get("codeowners", "CODEOWNERS"), "ARCHITECTURE.md", "REVIEW.md", "DEVELOPMENT.md"}
    for p in paths:
        parts = p.split("/")[:-1]
        files.update("/".join(parts[:n] + ["AGENTS.md"]) for n in range(len(parts) + 1))
    instr = org.get("instructions_dir", "")
    client.fetch(repo, sha, files=sorted(files), texts_in=[instr] if instr else [])


def agents_chain(client, repo: str, sha: str, path: str) -> List[str]:
    """AGENTS.md files from the repo root down to the directory holding `path` (those that exist)."""
    parts = path.split("/")[:-1]
    dirs = [""] + ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    found = [(d + "/" if d else "") + "AGENTS.md" for d in dirs]
    return [f for f in found if _text(client, repo, sha, f) is not None]


def _apply_to(frontmatter_text: str) -> List[str]:
    m = re.search(r"^---\s*\n(.*?)\n---", frontmatter_text, re.S)
    if not m:
        return []
    for line in m.group(1).splitlines():
        if line.strip().startswith("applyTo:"):
            raw = line.split(":", 1)[1].strip().strip("'\"")
            return [g.strip() for g in raw.split(",") if g.strip()]
    return []


def _doc(client, repo: str, sha: str, path: str, with_content: bool, limit: int = 6000) -> Optional[Dict[str, Any]]:
    text = _text(client, repo, sha, path)
    if text is None:
        return None
    out: Dict[str, Any] = {"path": path, "url": rules.blob_url(repo, sha, path),
                           "headings": [{**h, "url": rules.blob_url(repo, sha, path, h["line"])}
                                        for h in rules.headings(text)]}
    if with_content:
        out.update(doc_text(text, limit))
    return out


def knowledge_for(client, org: Dict[str, Any], repo: str, paths: List[str], ref: Optional[str] = None,
                  with_content: bool = True) -> Dict[str, Any]:
    ref_name, sha = resolve(client, repo, ref)
    prefetch(client, org, repo, sha, paths)
    label_rules = rules.label_rules_at(client, org, repo, sha)
    owners = rules.codeowners_at(client, org, repo, sha)
    instr_dir = org.get("instructions_dir", "")
    instr_files = [e["path"] for e in _dir(client, repo, sha, instr_dir) if e["name"].endswith(".instructions.md")] if instr_dir else []
    instr_globs = {f: _apply_to(_text(client, repo, sha, f) or "") for f in instr_files}

    documents: Dict[str, Dict[str, Any]] = {}
    per_path: List[Dict[str, Any]] = []

    def need(path: str) -> Optional[str]:
        if path not in documents:
            doc = _doc(client, repo, sha, path, with_content)
            if doc is None:
                return None
            documents[path] = doc
        return path

    top_docs = {k: p for k, p in (("architecture", "ARCHITECTURE.md"), ("review", "REVIEW.md"),
                                  ("development", "DEVELOPMENT.md")) if _text(client, repo, sha, p) is not None}
    for p in paths:
        entry: Dict[str, Any] = {"path": p}
        chain = [need(c) for c in agents_chain(client, repo, sha, p)]
        entry["agents_docs"] = [c for c in chain if c]
        entry["instructions"] = [f for f, gs in instr_globs.items()
                                 if any(rules.glob_match(g, p) for g in gs) and need(f)]
        sections: List[Dict[str, Any]] = []
        for kind in ("architecture", "review"):
            if kind in top_docs:
                text = _text(client, repo, sha, top_docs[kind]) or ""
                for s in rules.relevant_sections(text, p):
                    sections.append({"doc": top_docs[kind], "title": s["title"], "matched": s["matched"],
                                     "url": rules.blob_url(repo, sha, top_docs[kind], s["line"]),
                                     "anchor": f"{rules.blob_url(repo, sha, top_docs[kind])}#{s['anchor']}",
                                     "text": s["body"]})
        entry["doc_sections"] = sections
        entry["area_labels"] = rules.area_labels(label_rules, org, [p])
        entry["owners"] = rules.owners_for_path(owners, p)
        per_path.append(entry)

    return {"success": True, "repo": repo, "ref": ref_name, "sha": sha, "paths": per_path,
            "documents": documents,
            "guide_docs": {k: rules.blob_url(repo, sha, v) for k, v in top_docs.items()}}


# -- explain_path: how a file or folder fits into the code -------------------------------------------

_IMPORT_BLOCK = re.compile(r"^import\s*\(\s*(.*?)^\)", re.S | re.M)
_IMPORT_ONE = re.compile(r'^import\s+(?:\w+\s+)?"([^"]+)"', re.M)
_QUOTED = re.compile(r'"([^"]+)"')
_EXPORT = re.compile(r"^(?:func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)|type\s+([A-Z]\w*)\s+(struct|interface)\b)", re.M)
_PR_REF = re.compile(r"\(#(\d+)\)")


def go_summary(sources: Dict[str, str], module: Optional[str]) -> Dict[str, Any]:
    """Package name, what it imports and what it exports, from Go source text (no compiler needed)."""
    imports: Counter = Counter()
    exports, types, package = [], [], None
    for path, text in sources.items():
        m = re.search(r"^package\s+(\w+)", text, re.M)
        package = package or (m.group(1) if m else None)
        found = [i for blk in _IMPORT_BLOCK.findall(text) for i in _QUOTED.findall(blk)] + _IMPORT_ONE.findall(text)
        imports.update(set(found))
        for fn, ty, kind in _EXPORT.findall(text):
            (exports if fn else types).append(f"{fn or ty}" + (f" ({kind})" if ty else ""))
    internal = sorted(i for i in imports if module and i.startswith(module + "/"))
    external = sorted(i for i in imports if "." in i.split("/")[0] and not (module and i.startswith(module)))
    return {"package": package, "imports_internal": internal, "imports_external": external[:25],
            "imports_std": sorted(i for i in imports if "." not in i.split("/")[0])[:20],
            "exported_types": sorted(set(types))[:30], "exported_funcs": sorted(set(exports))[:40]}


def _importers(repo: str, module: Optional[str], directory: str, sha: str) -> Dict[str, Any]:
    """Who imports this Go package, from the clone `kyverno-workspace` made in the sandbox (GitHub's code search is too
    slow and incomplete for this). The clone may be at another commit than the one analysed; the answer says which."""
    clone = os.path.join(os.environ.get("KYVERNO_WORKSPACE_ROOT", "/sandbox"), repo.split("/", 1)[1])
    if not module or not os.path.isdir(os.path.join(clone, ".git")):
        return {"available": False, "note": "no clone in the sandbox yet: run `kyverno-workspace %s`, then ask again" % repo}
    package = module + ("/" + directory if directory else "")
    try:
        head = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        found = subprocess.run(["git", "-C", clone, "grep", "-l", "-F", "-e", '"%s"' % package, "--", "*.go"],
                               capture_output=True, text=True, timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "note": "could not search the clone: %s" % exc}
    outside = [f for f in found if os.path.dirname(f) != directory]
    folders = Counter(os.path.dirname(f) for f in outside if not f.endswith("_test.go"))
    return {"available": True, "package": package, "clone_commit": head[:7], "same_commit_as_analysed": head == sha,
            "importing_folders": [{"folder": d, "files": n} for d, n in folders.most_common(40)],
            "folders_total": len(folders), "test_files_importing": sum(1 for f in outside if f.endswith("_test.go")),
            "note": "from `git grep` in the clone's working tree; blank imports and generated files are included"}


def explain_path(client, org: Dict[str, Any], repo: str, path: str, ref: Optional[str] = None,
                 max_commits: int = 10) -> Dict[str, Any]:
    """Facts about a file or folder: what is in it, what it imports and exports (Go), its tests, the docs, labels
    and owners that govern it, and who has changed it recently. About three requests, two of them at once."""
    path = path.strip("/")
    ref_name, sha = resolve(client, repo, ref)
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    client.fetch(repo, sha, dirs=[path, parent])
    entries = _dir(client, repo, sha, path)
    is_dir = bool(entries)
    if not is_dir and path.rsplit("/", 1)[-1] not in _names(client, repo, sha, parent):
        return {"success": False, "error": f"{path} does not exist at {ref_name} ({sha[:7]})"}
    directory = path if is_dir else parent
    listing = entries if is_dir else _dir(client, repo, sha, directory)
    files = sorted(e["path"] for e in listing if e["type"] == "file")
    subdirs = sorted(e["name"] for e in listing if e["type"] == "dir")
    go_files = [f for f in files if f.endswith(".go") and not f.endswith("_test.go")]
    tests = [f for f in files if f.endswith("_test.go") or f.endswith(("kyverno-test.yaml", "chainsaw-test.yaml"))]
    kinds = Counter(f.rsplit(".", 1)[-1] if "." in f.rsplit("/", 1)[-1] else "(none)" for f in files)

    out: Dict[str, Any] = {
        "success": True, "repo": repo, "ref": ref_name, "sha": sha, "path": path, "kind": "folder" if is_dir else "file",
        "url": rules.blob_url(repo, sha, path), "directory": directory,
        "files_here": [{"path": f, "url": rules.blob_url(repo, sha, f)} for f in files[:40]],
        "file_types": dict(kinds.most_common(8)),
        "subfolders": subdirs[:60],
        "tests_here": tests[:20], "go_files": len(go_files),
    }
    code = [f for f in files if not f.endswith(".md")]
    rep = path if not is_dir else (code[0] if code else path + "/x")   # a real source file, not AGENTS.md
    picked = (go_files if is_dir else ([path] if path.endswith(".go") else go_files))[:15]

    def commits():
        return client.rest(f"repos/{repo}/commits?sha={sha}&path={path}&per_page=30")

    def source():
        if not picked:
            return None
        client.fetch(repo, sha, files=["go.mod", *picked])
        return {f: client.get_file(repo, f, sha) or "" for f in picked}

    # the history and the Go source are independent of each other and of the docs: fetch them together
    history, sources = client.parallel(commits, source)
    chain, real = knowledge_for(client, org, repo, [directory + "/_" if is_dir else path, rep], ref=sha,
                                with_content=False)["paths"][:2]
    out["governing_docs"] = {**chain, "path": path, "area_labels": real["area_labels"], "owners": real["owners"]}
    if sources:
        m = re.search(r"^module\s+(\S+)", _text(client, repo, sha, "go.mod") or "", re.M)
        out["go"] = go_summary(sources, m.group(1) if m else None)
        out["go"]["analysed_files"] = len(picked)
        out["imported_by"] = _importers(repo, m.group(1) if m else None, directory, sha)
    commits_ = history
    authors = Counter(((c.get("author") or {}).get("login") or c["commit"]["author"]["name"]) for c in commits_)
    out["history"] = {
        "recent_commits": [{"sha": c["sha"][:7], "url": c["html_url"], "date": c["commit"]["author"]["date"],
                            "author": (c.get("author") or {}).get("login") or c["commit"]["author"]["name"],
                            "title": excerpt(c["commit"]["message"].splitlines()[0], c["html_url"], 160),
                            "prs": [int(n) for n in _PR_REF.findall(c["commit"]["message"].splitlines()[0])]}
                           for c in commits_[:max_commits]],
        "most_active_authors_last_30_commits": [{"login": a, "commits": n} for a, n in authors.most_common(5)],
        "note": "people who changed this recently are who to ask or read; they are not its owners (see governing_docs.owners)"}
    return out
