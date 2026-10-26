---
name: workspace
description: "Work on code in the sandbox: clone the repo, get the right toolchain, reproduce an issue or a failing PR, make and test a change, and hand the result to the user as a pushed branch or a patch. Use it whenever a task needs code to be built, run or edited."
version: 0.1.0
author: Suhaani Agarwal
license: MIT
platforms: [linux, macos, windows]
---

# workspace

The terminal and file tools run inside an OpenShell sandbox: nothing on the user's computer is reachable, there
are no tokens in it, and it can write only under `/sandbox` and `/tmp`. Network access is GitHub reads plus the
download hosts for Go, Node, Go modules, npm packages and the Kyverno CLI and Chainsaw. Everything else is denied.

## When to Use

- "Reproduce this issue", "does this PR actually fix it?", "run the tests", "fix this failing check", "make the change".
- Not for facts about the code that GitHub can answer (history, reviews, owners): use the plugin tools, they are faster.

## Steps

1. **Set up once per repository.** `kyverno-workspace <owner/repo>` clones it to `/sandbox/<name>` and installs
   the toolchains it declares (Go from `go.mod`, Node from `.nvmrc` or `package.json`, the Kyverno CLI and Chainsaw
   for policy repositories), checking each download against the vendor's checksum. Tell the user the first run
   downloads a lot, and that the first Go build of kyverno/kyverno compiles for 10 to 15 minutes. Then
   `cd /sandbox/<name> && source /sandbox/.toolchains/env.sh` (new shells pick this up on their own).
   If the tools do not install, report which download failed and stop; do not work around the sandbox.
2. **Reproduce first.** For an issue, find the failing command in the report and run it. For a PR, fetch it and
   run what its CI runs: `git fetch origin pull/<n>/head:pr-<n> && git switch pr-<n>`. Read the project's
   `DEVELOPMENT.md` and CI workflow for the exact commands. Run the narrowest thing that shows the problem
   (`go test ./pkg/<package>/...`, one Chainsaw test, one `kyverno test` folder), not the whole repository.
   Show the command and the output; a reproduction that you did not run is not a reproduction.
   Memory is limited by Docker (check `free -m`): build the packages you need, not `./...`, and say so if a
   build is killed for lack of memory (the user can raise Docker's memory).
3. **Change the code** on a new branch (`git switch -c <issue>-<slug>`, never the default branch). Smallest change
   that fixes it, matching the surrounding code and tests. Do not hand-edit generated files: run the project's
   `make` target. Before editing a path, read what the repository says about it (`knowledge_for`).
4. **Prove it.** Run the same command again, plus the tests of the package you touched, and format/vet as the
   project does. Keep a ledger in `/sandbox/ledger-<name>.md`: every command, pass or fail with the key lines, and a
   "not verified" list for what the sandbox cannot run (cluster tests, end-to-end, other platforms). Never say
   something passed unless you ran it and saw it pass.
5. **Commit** with `git commit -s` (the project's CI requires a sign-off on every commit), conventional messages.
6. **Hand it over.** If `kyverno-workspace` printed "pushes go to your fork", the user turned pushing on: show them
   `git log --stat` and the branch name, and once they say yes, `git push -u fork <branch>`. A contributor can push to the `fork`
   remote only (their own fork). A maintainer can also push to `origin`, the repository they manage, to fix a PR branch.
   A hook refuses force pushes, deletes and pushes to main, master or a release branch, and the network rule refuses any
   other repository. Never try another remote or a workaround. Then tell them the compare link,
   `https://github.com/<their-login>/<repo>/pull/new/<branch>`, to open the PR (or open it for them when the GitHub tools
   allow it). If pushing is off, write patches instead:
   `git format-patch origin/HEAD.. -o /sandbox/<name>/patches`, and tell the user how to fetch and apply them:
   - OpenShell: `openshell sandbox download <sandbox> /sandbox/<name>/patches .` (see `openshell sandbox list`)
   - plain Docker: `docker cp <container>:/sandbox/<name>/patches .`
   - then in their own checkout: `git switch -c <branch> && git am patches/*.patch`, push to their fork, open the PR.
   Tell them once that `container/kyverno --push-to-fork` would let you push yourself.

## When something is blocked

A `403` from the proxy or a connection error on a host the task really needs: do not retry through another
program, encoding or port (those attempts are logged and refused on purpose). Ask for the narrowest rule:

```bash
curl -s -X POST http://policy.local/v1/proposals -H "Content-Type: application/json" -d '{
  "intent_summary": "<why, one sentence>",
  "operations": [{"addRule": {"ruleName": "<snake_case_name>", "rule": {
    "endpoints": [{"host": "<exact host>", "port": 443, "protocol": "rest", "enforcement": "enforce",
                   "rules": [{"allow": {"method": "GET", "path": "<narrowest path>"}}]}],
    "binaries": [{"path": "<the one program that needs it>"}]}}}]}'
```

Exact host, `GET` only, one program. Tell the user what you asked for and why. They approve it on their own
computer (`openshell rule approve --chunk-id <id> <sandbox>`); you cannot. Retry once it is approved, and if it
was rejected use their reason instead of asking again.

## Verification

- Every claim of "passes" or "reproduces" is backed by a command you ran, shown with its output.
- Commits are signed off; no commit is made on the default branch; no generated file is hand-edited.
- The handover is a pushed branch with its compare link, or the patch folder with the exact commands to fetch and apply it.
- The ledger has a "not verified" section whenever something could not be run here.
