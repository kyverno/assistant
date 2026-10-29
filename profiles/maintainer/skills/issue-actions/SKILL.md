---
name: issue-actions
description: "Issue decide-table execution: label/close/assign/milestone/nudge/raise, via maintainer token."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
---

# issue-actions Skill

Executes the maintainer's decision on an issue `issue-triage` surfaced, or a direct
instruction — using their own `REPO_GITHUB_TOKEN`. `issue_write` carries no labels-only
restriction here the way `pr-actions` restricts it for PRs: this skill operates on
real issues, where `state`/`assignees`/`milestone` are legitimate. That legitimacy
is also the risk — GitHub shares one number sequence between issues and PRs, and
`issue_write` will act on either, so every call here resolves the number as a real
Issue first.

## When to Use

- Maintainer decides on an issue `issue-triage` presented, or names an issue/action
  directly: confirm as bug/feature, mark duplicate, close out-of-scope, needs more
  info, assign, add to milestone, nudge a stalled PR's author, raise a new issue.
- Not for: classifying or relating issues — that's `issue-triage`. Not for: any
  action on a PR's own state/review — that's `pr-actions`.

## Prerequisites

- `kyverno-context` loaded this session (live label list, CODEOWNERS for hand-off
  candidates).
- `mcp-github` tools: `issue_write`, `add_issue_comment`, `update_issue_comment`,
  `issue_read`, `discussion_comment_write` (link-back reply when raising from a
  Discussion thread).
- `mcp-slack`: `conversations_add_message` — only if configured, for a Slack nudge
  or link-back reply.
- `REPO_GITHUB_TOKEN` scoped as in `distribution.yaml` — no merge, admin, or Contents-write;
  none of this skill's actions need any of those.

## Quick Reference

- `issue_write(method="update", owner, repo, issue_number=N, ...)` — the one
  multiplexed tool for every state-changing action below (`labels`, `assignees`,
  `milestone`, `state`, `state_reason`). `state_reason` enum is `completed` /
  `not_planned` / `duplicate` — there is no literal "out-of-scope" value; `not_planned`
  is the real one to use, cited in the comment instead.
- `issue_write(method="create", owner, repo, title, body, labels?, assignees?,
  milestone?)` — raises a new issue. No separate `create_issue` tool exists.
- `add_issue_comment(issue_number=N, body="...")` / `update_issue_comment(comment_id, body)`.
- `discussion_comment_write(method="add"|"reply", ...)` — the link-back reply when
  raising an issue from a Discussion thread; see `skills/discussions` for the full
  six-method reference. Only ever `add`/`reply` here.

## Guardrail: resolve the number before any state-changing call

`issue_read(method="get", issue_number=N)` succeeds for a PR number too, with no
`pull_request` key and a shape indistinguishable from a real issue — a successful
call is not confirmation. Check `html_url` instead: an issue's ends `/issues/<N>`,
a PR's ends `/pull/<N>`. Read that before any `issue_write` call carrying
`state`/`assignees`/`milestone` (labels-only is lower-risk but still check). If it's
`/pull/`, say so plainly and stop — don't let `issue_write` touch a PR's state.
`pr-actions` is the only skill that acts on a PR.

Every action below, including raising an issue, ends with `update_dashboard` (`actions`,
with the link the write produced) once it has landed — part of the action, not a
follow-up.

## Procedure: per-decision actions

Each is draft-then-confirm except where noted; show the maintainer the exact text
before any `add_issue_comment`/`discussion_comment_write` call.

- **Confirm as bug** / **Confirm as feature request**: the kind label is usually
  already there from the issue form — this action removes `triage` (the "still needs
  a maintainer decision" flag), and only adds/changes `bug`/`enhancement` if the
  template's own guess was wrong. Add the milestone too if the maintainer named one.
- **Mark duplicate**: draft "Duplicate of #X — [one-sentence why, from `issue-triage`'s
  comparison]." On confirmation: `add_issue_comment`, then `issue_write(state="closed",
  state_reason="duplicate", duplicate_of=X)`, together as one confirmed action.
- **Close as out-of-scope**: draft the comment explaining why. On confirmation:
  `add_issue_comment`, then `issue_write(state="closed", state_reason="not_planned")`.
- **Needs more info**: draft a comment asking the specific missing information. On
  confirmation: `add_issue_comment`, then `issue_write(labels=[...])` with whichever
  label's description (`kyverno-context`) matches — if none does, post the comment
  alone rather than inventing a label.
- **Assign to contributor**: `add_issue_comment` tagging them, then
  `issue_write(assignees=[login])`, together.
- **Add to milestone**: `issue_write(milestone=N)`.
- **Nudge a stalled PR's author**: the issue's linked PR (from `issue-triage`'s
  `closing_prs`) is `needs-author-action` and the issue read as urgent. Draft a nudge —
  `add_issue_comment` on the **PR's** number (a PR comment), or a Slack message via
  `conversations_add_message` if that fits better. Confirm, then post.

## Procedure: raise an issue

Two triggers (see `docs/workflow.md`'s Issue work §5): surfaced during Slack/Discussion
reading with no open issue tracking a real problem, or requested directly.

1. Draft: title, body (the problem, reproduction context from the source, and a link
   back to the source Slack message or Discussion thread when there is one),
   classification guess with labels from `kyverno-context`'s live list.
2. If the source was a Slack message or Discussion thread, draft the link-back reply
   too (`discussion_comment_write(method="add"|"reply", ...)` or
   `conversations_add_message`) — shown alongside the issue draft, not separately.
3. Maintainer confirms or edits the full draft in one pass.
4. On confirmation: `issue_write(method="create", ...)`, then post the link-back reply
   if one was drafted — together, as one confirmed action.

Completion criterion: nothing posts or creates before the maintainer has seen the
exact text, and a raise-issue draft from a triage finding cites the real source
(message/thread) it came from, never a paraphrase of "something was mentioned."

## Verification

- Ask to close an issue that's actually a PR number: confirm the `html_url` check
  catches it and the action stops, naming the mismatch.
- Mark duplicate: confirm the comment and the state-change post together as one
  confirmed action, not two separate asks.
- Needs more info on a repo with no matching label: confirm it posts the comment and
  says plainly no label exists, rather than inventing `needs-more-info`.
- Raise an issue from a Slack-surfaced finding: confirm the draft cites the real
  message, and the link-back reply is shown alongside the issue draft before either
  posts.
- Raise an issue directly requested, no source thread: confirm no link-back reply is
  drafted (nothing to link back to), just the issue itself.
