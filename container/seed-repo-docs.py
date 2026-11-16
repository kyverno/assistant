#!/usr/bin/env python3
"""Write a Mnemosyne import file holding the repository's AGENTS.md and ARCHITECTURE.md files, one entry each with
the id repo-doc:<path>, so the maintainer's skills can recall a package's own docs on a fresh sandbox.

    kyverno-seed-repo-docs owner/repo OUTPUT.json

Run with Hermes' Python: the proxy puts the real token in only for that interpreter. Prints one status line; exits 0
with nothing written when the repository has no such files or cannot be read.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

DOC = re.compile(r"(?i)(^|/)(agents|architecture)\.md$")


def get(path):
    req = urllib.request.Request(f"https://api.github.com/{path}", headers={
        "Authorization": "Bearer " + os.environ.get("REPO_GITHUB_TOKEN", ""),
        "Accept": "application/vnd.github+json", "User-Agent": "kyverno-assistant"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def main():
    repo, out = sys.argv[1], sys.argv[2]
    try:
        branch = get(f"repos/{repo}")["default_branch"]
        tree = get(f"repos/{repo}/git/trees/{branch}?recursive=true")
    except (OSError, urllib.error.URLError, KeyError, ValueError) as exc:
        print(f"could not list {repo}: {exc}")
        return
    paths = [n["path"] for n in tree.get("tree", []) if n.get("type") == "blob" and DOC.search(n["path"])]
    entries, failed = [], []
    for path in paths:
        try:
            text = base64.b64decode(get(f"repos/{repo}/contents/{path}?ref={branch}")["content"]).decode("utf-8")
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            failed.append(path)
            continue
        entries.append({"id": f"repo-doc:{path}", "content": f"--- {path} ---\n{text}", "source": "install-seed",
                        "timestamp": "2026-01-01T00:00:00", "session_id": "install-seed", "importance": 0.9,
                        "scope": "global", "pinned": 1})
    if not entries:
        print("no AGENTS.md or ARCHITECTURE.md files" if not paths else f"could not read {', '.join(failed)}")
        return
    with open(out, "w") as f:
        json.dump({"mnemosyne_export": {"version": "1.3"}, "working_memory": entries}, f)
    print(f"{len(entries)} repository docs cached" + (f", {len(failed)} unreadable" if failed else ""))


if __name__ == "__main__":
    main()
