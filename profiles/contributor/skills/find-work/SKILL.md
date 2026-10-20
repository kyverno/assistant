---
name: find-work
description: "Choose what to work on: issues matched to the contributor's areas, with the full history of each before recommending it, and how to join or adopt work already in progress."
version: 0.1.0
author: Suhaani Agarwal
license: MIT
platforms: [linux, macos, windows]
---

# find-work

## When to Use

- "What should I work on?", "find me an issue", "is #N worth starting?", "can I take over that stale PR?"

## Steps

1. **Open PRs first.** From `journey_summary`, if the contributor has open PRs that need them
   (`my_pr_state`), say so. Finishing one is usually better than starting another.
2. **Candidates.** `find_issues` with `known_areas` from `journey_summary` (the `area/...` labels). Tiers:
   `free` (no assignee, no open PR), `claimed` (assigned: the claim may be idle), `has_open_pr`.
   Real projects often have no free entry-level issue; do not stop at `free`.
3. **History before any recommendation.** For each issue you might suggest (at most 3), call `issue_history`.
   Report from it, with links:
   - every earlier PR: how it ended, who closed it, the closing comment (quote it), approvals, size, tests;
   - assignees and whether they look active (days since activity);
   - maintainer comments; `signals` lists the facts.
   Do not call an issue "good" or "bad"; say what the history shows and what it implies.
4. **Pick the path.** Offer the options that apply, and let the contributor choose:
   - an open PR exists: comment on the issue offering to help or add tests (draft the comment; if posted by
     you, `add_issue_comment` after a yes, no commands in it);
   - `adoptable` PRs: how much is done (size, tests, approval), what the close note said;
   - claimed and idle: ask on the issue whether the assignee is still working on it;
   - free: if the issue carries the allowed label, the contributor may self-assign with a bare `/assign`
     (the guard hook allows exactly that).
5. **Say what it teaches and what comes next.** Use `explain_path` on the likely folder:
   "touches pkg/webhooks/resource/mpol, which imports ...", and `related_prs` for how big similar changes were.
   Then name the natural next step (a neighbouring package, a review).
6. When they choose, `journey_note` kind `in_flight` with the issue link, then move to `understand-code`.

## Verification

- Asked for work it returns candidates from `find_issues` and calls `issue_history` on each one it
  recommends; no recommendation without the history.
- On an issue whose earlier PRs were closed with "collaborate with the PR authors" it quotes that comment and
  offers to help on the open PR instead of starting a new one.
- It never says an issue is "easy"; it cites the history and similar PRs.
- It does not post `/assign` itself unless the body is exactly `/assign` and the contributor said yes.
