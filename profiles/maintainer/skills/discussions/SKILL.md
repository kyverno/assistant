---
name: discussions
description: "Reads and answers GitHub Discussions via the maintainer's token."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
---

# discussions Skill

Reads contributor Discussions on `KYVERNO_REPO` and answers or replies to
them using the maintainer's own token. A separate skill from `pr-actions`
because answering a discussion requires synthesizing an answer — analysis,
not just executing a named action — which `pr-actions` deliberately never
does.

## When to Use

- Maintainer asks about open discussions, or to summarize/triage them.
- Maintainer asks to answer or reply to a specific discussion.
- Don't use for: PRs or issues — those are `pr-queue`/`pr-actions`.
  Discussions and issues are different GitHub objects with different tools;
  don't reach for `issue_read`/`issue_write` on a discussion number.

## Prerequisites

- `mcp-github` tools: `list_discussion_categories`, `list_discussions`,
  `get_discussion`, `get_discussion_comments`, `discussion_comment_write`.
- `search_code`/`search_issues` (already granted) for cross-referencing a
  discussion against related code or issues, on demand.
- `mnemosyne_recall`/`mnemosyne_remember` — see `kyverno-context`'s "what
  this agent remembers, and where" reference; used only at the points
  below.
- Env: `KYVERNO_REPO`, `MAINTAINER_GITHUB_LOGIN`.

## Quick Reference

- `list_discussion_categories(owner, repo)` — category id/name pairs.
  `repo` optional: omit for org-level categories, include for repo-level.
- `list_discussions(owner, repo, category?, orderBy?, direction?, perPage?, after?)`
  — the candidate set. `repo` optional, same org-vs-repo distinction as
  above. No `state`/`is:open` filter exists in this tool's schema — a
  closed/answered discussion is not excluded automatically; check
  `get_discussion` for state before treating one as still open.
- `get_discussion(owner, repo, discussionNumber)` — one discussion's body
  and metadata. All three params required.
- `get_discussion_comments(owner, repo, discussionNumber, includeReplies?, perPage?, after?)`
  — top-level comments; `includeReplies: true` nests each comment's replies
  inline (up to 100 per comment, GitHub's own cap). Defaults to `false` —
  pass it explicitly when a reply's content matters, not just top-level
  comments.
- `discussion_comment_write(method, ...)` — multiplexed, six methods, granted
  as one tool with no per-method split at the server or Hermes layer:
  - `"add"` — a new top-level comment. Needs `owner`, `repo`,
    `discussionNumber`, `body`.
  - `"reply"` — replies to a top-level comment. Needs `owner`, `repo`,
    `discussionNumber`, `commentNodeID` (the top-level comment's node ID,
    from `get_discussion_comments`), `body`. GitHub Discussions only
    support one level of nesting — you can reply to a top-level comment,
    never to a reply itself; there is no such thing as a nested reply here.
  - `"update"`/`"delete"`/`"mark_answer"`/`"unmark_answer"` — exist on this
    tool's schema but are out of this skill's normal scope (see Pitfalls).

## Procedure: answer or reply to a discussion

1. `get_discussion` for the body, `get_discussion_comments` (with
   `includeReplies: true` if the thread has replies worth reading) for the
   existing conversation — read the whole thread before drafting, not just
   the original post. After answering, push the thread's item to the
   dashboard (`needs_you` = the decision pending; `dashboard-sync`).
2. If the question touches the codebase, cross-reference with `search_code`
   (repo docs, relevant symbols) or `search_issues` (related issues/PRs) —
   cite what was actually found, same rule as `pr-queue`'s open-ended
   questions: don't answer from a general prior about what the repo
   probably does. Also `mnemosyne_recall` for whether a similar question
   was already answered before — reuse a durable answer for consistency,
   or flag the discrepancy if this one's premise differs.
3. Draft the reply and show it to the maintainer before posting — same
   confirm-then-post pattern used for a Slack PTAL reply. A discussion
   answer is visible to the whole community reading that thread, not just
   the maintainer; higher stakes than a PR label or an internal comment.
4. On confirmation, post via `discussion_comment_write`: `method="add"` for
   a new top-level comment, `method="reply"` when responding to a specific
   existing comment (get its `commentNodeID` from step 1's
   `get_discussion_comments` call first), then log it with `update_dashboard`
   (`actions`).
5. If the answer covers a genuinely new, durable point (not something
   already `recall`ed in step 2), `mnemosyne_remember` it — a one-line
   summary of the question pattern plus the answer, so a similar future
   question doesn't need re-deriving from scratch. Skip this for
   discussion-specific trivia that won't recur.

Completion criterion: the maintainer saw the exact reply text before it
posted, and confirmed it — never post first and report after.

## Pitfalls

- `discussion_comment_write` is granted as one tool covering all six
  methods — there is no way to grant just `add`/`reply` at the config
  layer, the same situation `issue_write` is already in for labels vs.
  state/assignees/milestone. Only ever use `add`/`reply` unless the
  maintainer explicitly asks for `update`/`delete`/`mark_answer`/
  `unmark_answer` by name — don't reach for those on your own initiative
  even though the tool technically allows it.
- `reply` needs the top-level comment's `commentNodeID`, not the
  discussion's own ID and not a reply's ID — GitHub Discussions have
  exactly one level of nesting, so there's no such thing as replying to a
  reply; if asked to, reply to the same top-level comment instead and say
  so.
- `list_discussions` has no open/closed or answered/unanswered filter —
  check each candidate's actual state via `get_discussion` before
  presenting it as needing attention.
- `owner` alone (no `repo`) queries at the *organization* level for
  `list_discussion_categories`/`list_discussions` — don't pass just
  `owner` expecting repo-scoped results.

## Verification

- Ask to answer a discussion and confirm the reply is shown and confirmed
  before `discussion_comment_write` is ever called.
- Ask to "mark this comment as the answer" and confirm it treats that as
  an explicit, named request — not something it does unprompted while
  replying.
- Ask about a discussion that references a symbol in the codebase and
  confirm `search_code` is actually called, with the citation shown, not a
  guess about what the code probably does.
- Ask a question similar to one already answered and confirm it recalls
  and reuses that earlier answer instead of re-deriving a new one.
