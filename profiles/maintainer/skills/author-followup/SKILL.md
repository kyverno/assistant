---
name: author-followup
description: "Stale/author-blocked PR decisions: close, reassign, needs-help; and the before-stale status check."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
---

# author-followup Skill

`pr-queue` covers PRs waiting on the maintainer. This is the other half: PRs
labelled `needs-author-action`, and the ones the stale sweep has since marked
`stale` — which the sweep itself never closes, so someone has to decide.

## When to Use

- Maintainer asks about stale PRs, author-blocked PRs, or a decision on one.
- `pr-queue` offered this after finding stale PRs in the same scope and the
  maintainer accepted.
- Not for: the maintainer's own review queue — `pr-queue`. Not for: actually closing
  a PR or writing to an issue — see Executing a decision below.

## Prerequisites

- `kyverno-context` loaded (CODEOWNERS, for needs-help hand-off candidates).
- `fetch_pr_candidates(..., include_stale_detail=true)` (plugin tool,
  `plugins/kyverno-fetch/`) — adds `maintainer_can_modify`, `staled_at`,
  `last_commit_at`, `author_comments_since_stale`, and per closing issue its real
  `assignees` and `other_closing_prs`. `detail="digest"` (capped at 60) drops
  body/review text for ranking a whole stale set by something search can't
  express directly (superseded, longest-silent) — pair it with `fetch_pr_details
  (..., include_stale_detail=true)` for just the chosen numbers' full data.
- `pr-actions` tools (`add_issue_comment`, `issue_write` labels-only) for drafting
  and posting the recommendation comment.
- `issue-actions`' guardrail and assign/unassign action — the real write for
  "Reassign," on the closing issue, never the PR.
- Env: `KYVERNO_REPO`.

## Procedure: stale PR decisions

**Step 0 — establish a focus before fetching.** The stale set can be large, so a
request that doesn't narrow it (by a milestone, an author, a count, or "all of
them") gets starting points first, before any tool call. For example: the 10 oldest,
the 10 already superseded by a merged PR, the 10 whose author has been silent the
longest, the 10 closing a milestone-tracked issue. The maintainer can name any other
slice. Fetch once they choose.

1. **Fetch.**
   - **A named focus, or "10 oldest"**: a direct search qualifier (`label:stale`
     plus `milestone:"..."`/`author:...`/`sort:created-asc`) plus `limit=10` —
     one `fetch_pr_candidates(..., include_stale_detail=true)` call.
   - **A relational default** (superseded, longest-silent, milestone-tracked):
     none of these are search qualifiers GitHub has — they all depend on the
     *closing issue's* state, not a PR-level field. `fetch_pr_candidates(
     search_query="label:stale", detail="digest", include_stale_detail=true,
     limit=60)` over the whole stale set, filter/rank client-side (an
     `other_closing_prs` entry `MERGED`; longest gap since the last
     `author_comments_since_stale` entry; `closing_issues[].milestone_open`),
     take the top 10, then `fetch_pr_details(repo, pr_numbers=[...],
     include_stale_detail=true)` for just those 10. State the slice size
     against the digest's own `total_count`, and against how many matched the
     filter before the cut to 10.
2. **Classify by `author_comments_since_stale`**: empty → silent (≥7 days since
   `staled_at` is ready for a decision below; <7 days is counted with days left,
   not decided yet). Non-empty → read them: a progress update is counted, not
   listed; asking for help is **Needs help**; "can't continue" is **Declined** —
   decide below, same as silent≥7d.
3. **Decide, per `closing_issues` entry**, for Declined/Silent≥7d:
   - Issue `state` closed, or an `other_closing_prs` entry `MERGED` → **Close**
     (superseded) — cite the PR/issue state.
   - An `other_closing_prs` entry still `OPEN` → **Close** in favor of that one,
     both cited.
   - Issue open and wanted (`milestone_open`, a bug/regression-type label, or
     already `help wanted`) → **Reassign**: unassign this PR's author from the
     issue if assigned, offer it to the path owners (`kyverno-context` CODEOWNERS
     on `changed_files`) or via `help wanted`. If `maintainer_can_modify` and the
     PR itself reads close to done (CI green, few unresolved threads), also offer
     **take over the branch** as an alternative to reassigning fresh.
   - Issue open, no real signal of demand → **Close** with the reason, invite a
     reopen.
   - No `closing_issues` entry → judge the PR on its own merits (does main already
     have this, is it still wanted) — lower confidence, say so, ask rather than
     assert.
4. **Needs help**: name what they said they're stuck on and the real path owners —
   never just "needs help" with no one named.
5. Every recommendation states what the PR would have achieved and whether that's
   still needed, from the body/diff/issue — never a bare verdict with no reason.
6. **After writing the answer, push to the dashboard** — items (verdict rescue / decide /
   close, `draft` for any close or nudge comment), then the `stale` view, and the
   one-line note (`dashboard-sync`).

## Procedure: needs-author-action status (on request, before staling)

`fetch_pr_candidates(repo=KYVERNO_REPO, search_query="label:needs-author-action")`
— `include_stale_detail` stays false, nothing has staled yet. Per PR: the blocker
kind (the readiness comment's machine-readable marker, via `pull_request_read`),
days until `stale` from the last author activity, and which PRs have had the
author reply or push since — flag those as likely back in the maintainer's court.

## Executing a decision

- **Close**: draft the comment (`add_issue_comment`) citing the superseding PR or
  issue state. No tool anywhere in this profile closes a PR — same wall as merge.
  Say so plainly; the maintainer closes it in GitHub.
- **Reassign**: the real write is on the issue, not the PR — use `issue-actions`'
  assign/unassign action and its `html_url` guardrail. Draft the "offered to X"
  comment on the PR only after the issue-side change is confirmed, not before.
- **Needs help**: draft a comment naming the blocker and the path owners, or a
  Slack message if that fits better.
- **Take over the branch**: no file-write tool exists in this profile today — say
  so if asked, don't improvise a substitute via a comment.
- **Something else**: offer whichever of the above actually fits, not a fixed
  list — same pattern as a PR review brief's deep-dive menu.

Log each executed decision with `update_dashboard` (`actions`), then check what it unblocks and offer that next — a reassigned
issue may now be worth re-triaging, a closed PR may free up a milestone issue
`issue-triage` should revisit.

## Pitfalls

- `author_comments_since_stale` only looks at comments after `staled_at` — a
  silent author who commented right *before* staling isn't "still working," the
  sweep already judged that silence by labelling it stale.
- `other_closing_prs` can include issues the PR itself doesn't know about — always
  cite both PR numbers when recommending Close-in-favor-of.
- Never call `issue_write` with `state`/`assignees` on the PR's own number — that's
  `pr-actions`' restriction, and the real write here is always on the issue.

## Verification

- A stale PR whose closing issue has a merged `other_closing_prs` entry:
  recommends Close, cites that PR by number.
- A stale PR whose closing issue has two other *open* competing PRs: both named,
  not silently picked between.
- An author's "I'm stuck on X" comment: produces Needs help naming X and the real
  CODEOWNERS path owner, not a generic nudge.
- Asked to reassign: confirm the write lands via `issue-actions` on the issue
  number, never the PR number.
- Asked to close a PR directly: confirm it drafts the comment and states plainly
  that closing itself needs the maintainer, rather than attempting any tool call.
