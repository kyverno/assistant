---
name: review-digest
description: "The weekday review digest posted to Slack: what changed, the PRs in merge order with what to do with each, the author-blocked ones that matter, competing PRs, issues to look at, and what to close or reassign."
version: 0.3.0
author: Suhaani Agarwal
license: Apache-2.0
platforms: [linux, macos, windows]
---

# review-digest Skill

For the scheduled `review-digest` job, which posts to Slack with nobody to ask. A maintainer reads it once, in the
morning, and knows what to merge, what to chase, what to close and what to look into. It is the `pr-queue` ranking in a
fixed, bounded form. Everything in it comes from the tool calls below; a claim with no source is left out.

## Procedure (these calls, in this order, and no others)

1. `repo_pack`: the ready labels (`readiness.ready_labels`), the attention labels (`readiness.attention_labels`), the
   severity labels of issues, and whether a branch gate is declared. No ready labels: post one line saying the
   repository has not adopted them, and stop.
2. Ready PRs: `fetch_pr_candidates(repo=ASSISTANT_REPO, search_query="label:<ready labels joined by commas>",
   detail="digest", limit=40)`. A comma between labels means either one. Keep `total_count`.
3. Author-blocked PRs: `fetch_pr_candidates(search_query="label:needs-author-action", detail="digest", limit=20)`.
4. Stale PRs: `fetch_pr_candidates(search_query="label:stale", include_stale_detail=true, limit=8)`. This one adds the
   assignees of the closing issue and `author_comments_since_stale`.
5. `sequence_prs` on the ready PRs from step 2: tiers, `unresolved` (two PRs closing one issue), `file_overlaps`,
   `package_overlaps`, `gate_blocked`. If the pack declares a gate, first `list_issues(state="open",
   labels=["<gate failure label>"])` and pass the branches as `blocking_issue_branches`.
6. Issues: `fetch_issue_candidates(search_query="is:open label:<the pack's critical and high severity labels, comma
   joined>", detail="digest", limit=15)`. Use `closing_prs` to see which have no PR yet.
7. Since last time: your previous digest is in context. Take the PR numbers it listed that are not in any set above and
   call `fetch_pr_details` once for them (at most 10). `state` and `merged_at` say merged or closed. No previous
   digest: leave "Since the last digest" out.

No `fetch_file_diff_overlap`, diffs, Slack, Discussions, dashboard or memory calls. If a call fails, drop the section
it feeds and carry on; never explain.

## Ranking the merge order

Order the ready PRs the way `pr-queue` does. Hard dependencies from `sequence_prs` first: a PR never appears before the
PR it depends on (stacked, generated file inputs, "depends on"). Inside a tier, group PRs joined by an overlap or by one
closing issue and keep them adjacent, then rank by this ladder, each level only breaking ties in the one above:

1. Severity of the closing issue: critical (crash, outage, data loss, security) > high (regression) > medium (bug) >
   low (enhancement, tests, docs).
2. A release-critical label on the closing issue, then the nearer `milestone_due_on`. The milestone the most ready PRs
   belong to is the current focus; say its name and due date once.
3. Merge-ready before not: green CI, `bot_approved`, no open threads.
4. Fewer overlaps with other ready PRs: a PR that touches nothing else lands before one that overlaps with three.
5. Smaller first.

Readiness is separate from priority: a critical PR with red CI or requested changes keeps its rank but is marked
"fix first", never "merge now". Large PRs (many files) are marked "needs a dedicated review".

For each PR work out what to do, using the first line that fits: `gate_blocked` (state it, it is branch-wide); red CI
("author to fix CI"); `changes_requested`; `merge_state` DIRTY ("needs a rebase"); unresolved threads ("N threads
open"); green and `bot_approved` with no open threads ("merge now" with at least one human approval, "approve, then
merge" with none: the pack states no approval count, so say how many approvals it has); otherwise "needs a first review".

Who: `author`, with `author_association` in words: MEMBER "maintainer", COLLABORATOR "collaborator", CONTRIBUTOR
"contributor", FIRST_TIME_CONTRIBUTOR or FIRST_TIMER "first-time contributor", NONE "new contributor"; Dependabot is
"bot". For a first-time contributor add a short prompt ("give a warm, specific first review"). The role is shown for
context and never moves a PR up or down beyond the ladder.

## What goes in each section

- **Merge order**: 10 to 15 PRs a maintainer can act on today (fewer only if fewer qualify). A PR with red CI,
  requested changes, a conflict or a closed-over issue is not a merge candidate: it goes to "Needs author action" or
  "Close or reassign", whatever its priority. Name a release-critical label as "release-critical", not any other way. Dependabot PRs are not listed one by one: one line
  after the list. Two lines per PR at most: the PR with its author, role, severity and action; then, only when it
  matters, a `↳` line for what it is tied to (related or competing PR, shared file, depends on). Say "touches the same
  file as #N" for an overlap; never claim two PRs will conflict.
- **Needs author action, and matters**: up to 5 from step 3 with a critical or high closing issue or a near milestone.
  Say what the author must do and how long it has waited. If the author has been silent for a long time and the issue
  is critical, say someone else could pick it up.
- **Related and competing**: only from `unresolved` and overlaps. Name the PRs, what they share, which one to keep and
  why (more complete, green, approved, earlier), and what to do with the other.
- **Issues to look at**: up to 5 from step 6: critical or high, open, with no PR in `closing_prs`, or in the nearest
  milestone. One line: what it is, why now, who is assigned or "nobody".
- **Close or reassign**: up to 5 from step 4 and from the other sets. A PR qualifies when: it carries the stale label
  and `author_comments_since_stale` is 0; or its closing issue has `state` CLOSED (already fixed elsewhere); or
  another open PR closes the same issue and is clearly the better one. For each, give the action: close with a
  thank-you, ask the author, or reassign (name the closing issue's assignees from `closing_issues`; if the assignee is
  the stale author, say the issue is free for someone else).
- **Heads-up**: at most three lines: a branch gate that blocks merging, a failing infra check, a PR that has waited the
  longest, a pile-up of PRs in one package.

## Output

Only the digest, nothing before or after, in Slack-friendly Markdown that Hermes converts: bold section titles with one
emoji each, short numbered lists, real links `[#n](https://github.com/<repo>/pull/<n>)`, no tables, no headings, no
code fences. Between 3,500 and 7,000 characters, never more than 7,500; if over, cut the lowest-ranked lines, not
sections. Titles are shortened to what the PR does.

```
**Review digest · kyverno/kyverno · Tue 18 Nov**
42 ready · 5 merged, 2 closed, 6 new since Monday · focus: 1.17 (due 28 Nov), 9 ready PRs

**📋 Since the last digest**
Merged: #18252 chi v5.4.0 (CVE) · #18234 namespaceSelector fix · …
Closed unmerged: #18100 · New and ready: #18301, #18305

**🔀 Merge order** (top to bottom; hard dependencies and overlaps respected)
1. [#18252](url) chi v5.4.0, CVE fix · @renovate-user (maintainer) · critical · **merge now**, green, bot-approved
2. [#18290](url) CEL cache for policies · @alice (maintainer) · high, 1.17 · **approve, then merge**, 1 approval so far
   ↳ touches the same file as #18291; merge it first, then rebase this one
3. [#18305](url) fix webhook timeout · @bob (first-time contributor) · medium · **needs a first review**, give a warm one
…
Dependabot: 5 more bumps, 3 green, #18400 go-jose v4 is major

**✋ Needs author action, and matters**
- [#18613](url) E2E gate hardening · @carol (maintainer): red CI (conformance), waiting 8 days. Nudge or take over.

**⚔️ Related and competing**
- [#18420](url) and [#18455](url) both close #17990 · keep #18455 (green, approved), ask #18420's author to close theirs.

**🐞 Issues to look at**
- [#17990](url) policy reports leak memory · critical, 1.17, nobody assigned, no PR yet

**🧹 Close or reassign**
- [#18010](url) stale 40 days, author silent: close with thanks, or reassign #17800 (free: the assignee was the author).

**⚠️ Heads-up**
- The E2E gate is open on main: merging is blocked until it is fixed.
```

- The last line of the post is the digest itself: no note about the dashboard, memory or the scheduled run.
- Leave out a section that would be empty. Never write what you did not check, how the list was built, or that
  anything was skipped. If the whole thing fails: one line, "Could not build the digest: <reason>".
- No ready PRs and nothing else to report: "Nothing is waiting for review."
