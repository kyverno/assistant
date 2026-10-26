---
name: make-the-change
description: "Change the code in the sandbox, prove it with evidence, check it against the project's own review rules, and prepare the PR so reviewers have little to ask. The contributor writes the AI-use line and all replies."
version: 0.1.0
author: Suhaani Agarwal
license: MIT
platforms: [linux, macos, windows]
---

# make-the-change

## When to Use

- The contributor has chosen an issue and passed the explain-back in `understand-code`, and now wants to
  write, test and prepare the change.

## Prerequisites

- The sandboxed terminal and file tools (set up by `scripts/install.sh`). Without them you can still plan
  the change and review code through GitHub, but not edit or run anything: say so.
- The sandbox image has git, make, ripgrep and python3. **It does not have Go yet**, and the network policy
  allows GitHub reads only. If `go` is missing, say so plainly: edit and review, give the exact commands the
  contributor must run on their own machine, and never report a build or test as passed that you did not run.

## Steps

1. **Workspace.** `git clone --depth 50 https://github.com/<upstream>.git /sandbox/<name>`, then
   `git switch -c <issue-number>-<short-slug>`. Never work on the default branch.
2. **Read the rules for the path.** `knowledge_for` for the files you will touch: nested AGENTS.md, the
   instructions file, the generated zones not to edit by hand, REVIEW.md sections. Also read the root
   `DEVELOPMENT.md` for the commands and `.github/pr_documentation.md` if behaviour changes.
3. **Make the smallest change that fixes the issue.** Match the surrounding code (naming, comments,
   error handling, test style). Add or extend tests next to the code, following the neighbouring tests. Do
   not touch unrelated files; do not edit generated files, regenerate them with the project's `make` target.
4. **Prove it.** Keep an evidence ledger in `/sandbox/ledger-<issue>.md`: each command you ran, its result
   (pass/fail with the key lines), tests added, and a "not verified" list. Run what the sandbox supports
   (formatting, vet, unit tests for the touched packages, codegen) and list the rest for the contributor or
   the fork's CI. A reproduction before the fix and the same command after it is the best evidence.
5. **Commit with sign-off.** `git commit -s` for every commit (the project's CI requires a DCO per commit).
   Conventional, specific commit messages. The project's AI policy (`ai_policy` in the org file) asks for a
   disclosure trailer on AI-assisted commits, e.g. `Assisted-by: <tool> <email>` (REVIEW.md says the same):
   offer to add it, and let the contributor decide the wording. Before starting, check the issue is accepted
   (`accepted` in `find_issues`, no `triage` label) and that `journey_summary.open_pr_limit` leaves room.
6. **Self-review against the project.** Before the PR, check and report, with commands:
   - every commit signed off: `git log --format='%h %(trailers:key=Signed-off-by,valueonly)' origin/HEAD..`
   - diff size and scope: `git diff --stat origin/HEAD..` vs the issue; anything unrelated
   - no hand-edited generated files; codegen up to date if the project says so
   - tests added or updated; docs issue or doc change if behaviour changed
   - the checks in `.github/pr-readiness-checks.yml` (DCO and CI) and in REVIEW.md
   - what reviewers have flagged or decided before in this area: `related_prs` (`maintainer_voice`), then the linked threads
   - a quick scan of the diff for secrets and debug leftovers
   Fix what you find, or put it in the "not verified" list.
7. **Draft the PR description** from the repository's PR template (`.github/pull_request_template.md`), from the
   ledger: the issue link, what and why, before and after behaviour for user-visible changes, how it was
   tested (copy commands and results from the ledger), risks. **Leave the AI-assistance line and any
   statement of understanding for the contributor** to write, and remind them the trailer should match it.
8. **Deliver.** There is no push tool. Produce the patch with `git format-patch origin/HEAD..` and tell the
   contributor how to fetch it (`scp sandbox@<ssh host>:/sandbox/<name>/*.patch .`) and push it from their
   own fork. Once they open the PR, continue in `review-loop`.
9. `journey_note` kind `in_flight` with the PR link.

## Verification

- Every commit it makes carries a Signed-off-by line.
- The ledger lists commands and outcomes, and a "not verified" section whenever Go or the network was missing.
- The PR draft has the AI-assistance line empty with a reminder, and no invented test results.
- It does not edit generated files by hand and does not touch files unrelated to the issue.
- It never claims a test passed without running it.
