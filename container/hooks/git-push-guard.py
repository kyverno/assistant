#!/usr/bin/env python3
"""pre_tool_call hook for the terminal, shared by both profiles. Fails closed (config.yaml sets fail_closed: true).

The sandbox's network policy decides which repositories a `git push` can reach (your own forks, plus the managed
repository for a maintainer; see container/kyverno --push-to-fork). It cannot see inside the push, so this hook
covers the rest. A contributor pushes to the `fork` remote only; a maintainer may also push to `origin`, to fix a PR
branch of the repository they manage. Neither forces, deletes a branch, or pushes to main, master or a release
branch: a direct push to the default branch would skip the review and merge gate.

It also refuses a bare `git clone`: the agent must run `kyverno-workspace OWNER/REPO` instead, which clones the same
repository and also sets up the `fork` remote, the push credential, the sign-off setting and the toolchains. A plain
clone has none of that, so a later `git push` would fail and the commits would lack the sign-off.

Hermes pipes the event as JSON on stdin and expects JSON on stdout.
"""
import json
import os
import re
import shlex
import sys

PROTECTED = re.compile(r"^(main|master|release[-/].*)$")
# The contributor profile's folder is named kyverno-contributor; Hermes sets HERMES_HOME to the profile's folder.
REMOTES = {"fork"} if os.environ.get("HERMES_HOME", "").rstrip("/").endswith("contributor") else {"fork", "origin"}
FORCE_FLAGS = {"-f", "-d", "--force", "--delete", "--mirror", "--all", "--prune", "--tags"}


def block(message):
    print(json.dumps({"action": "block", "message": message}))
    sys.exit(0)


def check(tokens):
    """tokens: one command, from `git` on. Returns a reason to block, or None."""
    args = tokens[1:]
    # Skip git's own options (git -C dir push ...) to find the subcommand.
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i < len(args) and args[i] == "clone":
        return ("do not use git clone: run `kyverno-workspace OWNER/REPO` (for example kyverno-workspace kyverno/kyverno). "
                "It clones the repository into /sandbox and sets up your fork, sign-off and toolchains")
    if i >= len(args) or args[i] != "push":
        return None
    rest = args[i + 1:]
    positional = [a for a in rest if not a.startswith("-")]
    for a in rest:
        if a in FORCE_FLAGS or a.startswith("--force") or a.startswith("--receive-pack") or a.startswith("--repo"):
            return "git push %s is not allowed: no force, delete, mirror, all or tags pushes" % a
    if positional and positional[0] not in REMOTES:
        return "git push goes to %s only, not '%s'" % (" or ".join("'%s'" % r for r in sorted(REMOTES)), positional[0])
    for ref in positional[1:]:
        if ref.startswith("+") or ref.startswith(":"):
            return "git push '%s' would force or delete a branch" % ref
        target = ref.split(":")[-1].replace("refs/heads/", "")
        if PROTECTED.match(target):
            return "git push to '%s' is not allowed: push a feature branch and open a PR" % target
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command") or ""
        if "push" not in command and "clone" not in command:
            print("{}")
            return
        for part in re.split(r"[;&|\n]+", command):
            try:
                tokens = shlex.split(part)
            except ValueError:
                tokens = part.split()
            while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
                tokens = tokens[1:]  # leading VAR=value
            if tokens and tokens[0] == "git":
                reason = check(tokens)
                if reason:
                    block(reason)
        print("{}")
    except SystemExit:
        raise
    except Exception as exc:  # fail closed
        block("could not check this command (%s); not run" % exc)


main()
