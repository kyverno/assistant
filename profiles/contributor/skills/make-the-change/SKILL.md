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

- The sandboxed terminal and file tools, and the `workspace` skill: follow it for setting up the repository and
  toolchains, running commands, the evidence ledger, signed-off commits and the patch handover. This skill adds
  what is specific to contributing here.
- If the tools do not install or a command is refused, say so plainly, give the exact commands for the
  contributor's own machine, and never report a build or test as passed that you did not run.

## Steps

1. **Set up and branch** as in `workspace` (clone, toolchains, `git switch -c <issue-number>-<short-slug>`).
2. **Read the rules for the path.** `knowledge_for` for the files you will touch: nested AGENTS.md, the
   instructions file, the generated zones not to edit by hand, REVIEW.md sections. Also read the root
   `DEVELOPMENT.md` for the commands and `.github/pr_documentation.md` if behaviour changes.
3. **Reproduce, change, prove** as in `workspace`: reproduction before the fix and the same command after it is
   the best evidence. Add or extend tests next to the code, following the neighbouring tests. Do not touch
   unrelated files.
4. **Commit with sign-off** (`git commit -s`, every commit). Conventional, specific commit messages; no
   AI-assistance trailer unless the org file says so.
5. **Self-review against the project.** Before the PR, check and report, with commands:
   - every commit signed off: `git log --format='%h %(trailers:key=Signed-off-by,valueonly)' origin/HEAD..`
   - diff size and scope: `git diff --stat origin/HEAD..` vs the issue; anything unrelated
   - no hand-edited generated files; codegen up to date if the project says so
   - tests added or updated; docs issue or doc change if behaviour changed
   - the checks in `.github/pr-readiness-checks.yml` (DCO and CI) and in REVIEW.md
   - what reviewers have flagged or decided before in this area: `related_prs` (`maintainer_voice`), then the linked threads
   - a quick scan of the diff for secrets and debug leftovers
   Fix what you find, or put it in the "not verified" list.
6. **Draft the PR description** from the repository's PR template (`.github/pull_request_template.md`), from the
   ledger: the issue link, what and why, before and after behaviour for user-visible changes, how it was
   tested (copy commands and results from the ledger), risks. **Leave the AI-assistance line and any
   statement of understanding for the contributor**, with a note that they are theirs to write.
7. **Hand over** the patches as `workspace` describes. Once they open the PR, continue in `review-loop`.
8. `journey_note` kind `in_flight` with the PR link.

## Verification

- Every commit it makes carries a Signed-off-by line.
- The ledger lists commands and outcomes, and a "not verified" section whenever Go or the network was missing.
- The PR draft has the AI-assistance line empty with a reminder, and no invented test results.
- It does not edit generated files by hand and does not touch files unrelated to the issue.
- It never claims a test passed without running it.
