---
name: pr-actions
description: "Labels/comments/reviews/branch-update via maintainer token."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
---

# pr-actions Skill

Takes the actions a maintainer would take on a PR — using their own
`REPO_GITHUB_TOKEN`, never a privileged one — after `pr-queue` has surfaced what
needs doing. Label, comment, request changes, approve, and catch a branch up
with its base. Cannot merge: no merge tool exists in this profile's toolset
(see `docs/architecture.md`) — if a task seems to need merging, say so and
stop rather than looking for a workaround. Same answer for committing or
pushing a code change to a PR branch (e.g. a fix `pr-queue` proposed for a
Dependabot `needs-review` PR): no file-write tool is granted either, so name
that plainly and stop instead of improvising with a comment or a review
body as a substitute for an actual commit.

## When to Use

- Maintainer instructs a specific action on a specific PR: label it,
  comment on it, request changes, approve it, or bring it up to date with
  its base branch.
- Don't use for: deciding what needs doing — that's `pr-queue`. Don't use
  for: merging — there is no tool for it, here or anywhere else in this
  profile.

## Prerequisites

- `kyverno-context` loaded this session (codegen map, for the branch-update
  warning below).
- `mcp-github` tools: `issue_write`, `add_issue_comment`,
  `update_issue_comment`, `pull_request_review_write`,
  `add_comment_to_pending_review`, `add_reply_to_pull_request_comment`,
  `update_pull_request_branch`, `pull_request_read`, `actions_list`,
  `actions_get` (the fork-workflow-approval link, below).
- `list_discussions`, `discussion_comment_write` — a nudge posted as a
  Discussion reply, when a related thread exists.
- `mnemosyne_remember` — for Defer, below.
- `REPO_GITHUB_TOKEN` scoped as in `distribution.yaml` — no merge, admin, or
  Contents-write.

## Quick Reference

- `issue_write(method="update", issue_number=N, labels=[...])` — labels
  only; never pass `state`, `assignees`, or `milestone` on this call (see
  Pitfalls).
- `add_issue_comment(issue_number=N, body="...")` — a top-level comment.
- `update_issue_comment(comment_id=..., body="...")` — edit a comment
  already posted (e.g. a running queue-summary comment) instead of
  duplicating it.
- `pull_request_review_write(method="create", event="APPROVE" | "REQUEST_CHANGES" | "COMMENT", body="...")`
  — the review verdict. These `event` values are GitHub's standard REST
  convention this tool almost certainly wraps, but weren't independently
  re-confirmed against a live call in this research pass — verify on first
  real use (see Pitfalls).
- `add_comment_to_pending_review(path=..., line=..., body="...")` —
  line-specific feedback, before submitting the review.
- `add_reply_to_pull_request_comment(commentId=..., body="...")` — reply to
  an existing review thread, e.g. answering a Copilot/CodeRabbit comment.
- `update_pull_request_branch(pullNumber=N)` — the only "catch up with
  base" tool available. **This performs a merge of the base branch into
  the PR branch (a merge commit), not a git rebase** — confirmed against
  GitHub's own REST docs, which describe it as "merging HEAD from the base
  branch into the pull request branch." No tool here does a true rebase;
  that needs local git plus a force-push, which this toolset deliberately
  excludes (force-push is one of the patterns `hooks/block-dangerous-tools.sh`
  blocks). Say "update the branch" or "merge main in" to the maintainer,
  not "rebase" — match the language to the actual operation, even when the
  maintainer themselves says "rebase."

Every action below ends with `update_dashboard` (`actions`, with the link the write
produced) once it has landed — part of the action, not a follow-up.

## Procedure: label / comment / approve

1. Confirm current state with `pull_request_read` before acting — don't
   assume a label isn't already applied or a review wasn't already left.
2. Take the single requested action via the matching tool above.
3. Confirm the action landed and report back plainly what was done and to
   which PR — not a generic "done."

Completion criterion: the maintainer's instruction maps to exactly one
tool call of the right kind, and the result is confirmed, not assumed.

## Procedure: request changes

1. Draft the review body from the maintainer's notes.
2. Show the maintainer the exact draft; they confirm or edit.
3. On confirmation: `pull_request_review_write(method="create",
   event="REQUEST_CHANGES", body=...)`. Don't also apply
   `needs-author-action` — a review isn't a trigger for the real readiness
   workflow (fork PRs only get a read-only token, so a review couldn't
   write the label anyway); the hourly sweep picks up the unresolved thread
   and applies it within the hour. Applying it manually here would just be
   racing a mechanism that's about to do it anyway.

## Procedure: defer

No GitHub action. `mnemosyne_remember` the reason plainly ("deferred #N —
waiting on X"), not just "deferred" — so a later session knows why it was
set aside instead of re-deciding or forgetting it was looked at. Log it on the
dashboard as `deferred`.

## Procedure: flag conflict

1. Draft a comment naming the conflict plainly — e.g. "#X and #Y both close
   #N" for a duplicate-effort case `pr-queue`'s `unresolved` surfaced.
2. Maintainer confirms or edits.
3. `add_issue_comment` on the PR (or both PRs, if the conflict is mutual).

## Procedure: nudge the author

For a `needs-author-action` PR stalled on something urgent (a severe linked
issue, a near milestone) — raised proactively by `pr-queue`, or requested
directly.

1. Check for a related GitHub Discussion thread (`list_discussions`,
   client-side matched against the PR/issue number or topic — same check
   `pr-queue` already does). One exists and fits → draft a reply there.
2. Otherwise, draft a comment on the PR, or on its linked issue if the
   urgency actually lives there (a near milestone, a severe report) —
   whichever reads as the right place.
3. Maintainer confirms or edits.
4. Post: `discussion_comment_write(method="reply", ...)` for the Discussion
   route, `add_issue_comment` for the PR/issue route.

## Procedure: apply e2e-gate-bypass

Never applied by this skill on its own judgment — a deliberate human safety
call, not a missing tool (`issue_write(labels=[...])` could mechanically
apply it the same way as any other label). Explain why it's needed (cite
the real `e2e-failure` issue and why this PR reads as unrelated to it, from
`kyverno-context`'s e2e-gate facts) and name the exact label,
`e2e-gate-bypass`. The maintainer applies it themselves.

## Procedure: catch a branch up with base ("rebase" on instruction)

1. Before calling `update_pull_request_branch`, check the PR's changed
   files (`pull_request_read`, `get_files`) against `kyverno-context`'s
   codegen fan-out map. If the PR touches `api/**` or another codegen
   input, warn the maintainer up front: updating the branch will likely
   leave generated files stale, and the PR author will need to run the
   relevant `make codegen-*` target themselves afterward — this tool only
   updates the branch, it doesn't regenerate anything.
2. Call `update_pull_request_branch(pullNumber=N)`.
3. On success: report the update landed. If step 1 flagged a codegen
   concern, restate it — a successful branch update doesn't resolve it.
4. On failure (a real conflict the merge can't auto-resolve): don't retry
   blindly. Report it back as something the PR author needs to resolve
   locally — this tool cannot resolve a real conflict, and there is no
   fallback tool that can (see Quick Reference on why a true rebase isn't
   available here).
5. After either outcome, if the PR touches codegen inputs, re-check
   `get_files` for anything now in the generated-file set that wasn't in
   the PR's original diff — call that out explicitly as a likely-stale-
   generated-file signal, not an ordinary file change.

Completion criterion: the maintainer knows whether the update succeeded,
whether a codegen warning applies, and — on failure — that it's on the PR
author to resolve, not something to keep retrying.

## Pitfalls

- `issue_write` is broader than "just labels" — it can also touch `state`,
  `assignees`, and `milestone`. Only ever pass `labels` on this call; never
  `state`, even if asked something adjacent-sounding (e.g. "mark it
  ready") — say plainly that's outside this skill's action set instead of
  improvising with a broader field.
- "Rebase" is the word maintainers will use; `update_pull_request_branch`
  performs a merge-update, not a rebase — see Quick Reference. Don't let
  the word choice imply commit history gets rewritten; it doesn't, and
  nothing in this toolset can do that.
- Never attempt to reach `merge_pull_request` or any tool outside this
  skill's Quick Reference, even indirectly (e.g. a generic API-call
  request) — there is no escape hatch, and `hooks/block-dangerous-tools.sh`
  backstops this regardless of what's asked.
- Cannot re-trigger or detect post-merge CI (no polling in v1) — that's
  `pr-queue`'s conversational-input handling, not something this skill
  does.
- Asked to commit or push a fix `pr-queue` proposed (e.g. for a Dependabot
  `needs-review` PR): no file-write tool is granted, so say so and stop —
  same handling as the merge case above, not a reason to post the fix as
  a comment instead and call it done.
- `e2e-gate-bypass` is the one label this skill never applies itself, even
  though `issue_write` could mechanically do it — always explain and name
  it, never call the tool.

## Verification

- Ask to label a PR with a label that doesn't exist on the repo and
  confirm `kyverno-context`'s live label list is checked first, rather
  than calling `issue_write` with a made-up name.
- Ask to "rebase" a PR that touches `api/**` and confirm the codegen
  warning appears before the action runs, not only after.
- Ask this skill to merge a PR and confirm it declines and names the
  reason (no tool for it) rather than attempting a workaround.
- Ask this skill to commit a fix to a Dependabot PR and confirm it
  declines and names the reason (no file-write tool granted) rather than
  substituting a comment or attempting any other tool.
- Request changes on a PR: confirm only the review posts, no
  `issue_write` call follows it.
- Nudge an author on a PR with a related open Discussion: confirm it
  replies there rather than defaulting to a PR comment.
- Ask to apply `e2e-gate-bypass`: confirm it explains and names the label
  without calling `issue_write`.
