---
name: issue-triage
description: "Issue classification, issue↔PR relationship graph, progress synthesis, relationship/staleness surfacing."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
---

# issue-triage Skill

Reads and relates issues on `KYVERNO_REPO` — classification, which PR(s) address one,
how far along it is, and the wider issue graph. Relies on `kyverno-context` for live
labels/CODEOWNERS — assumed loaded, not re-derived here. Taking an action on an issue
(label, close, assign, raise a new one) is `issue-actions`' job, not this skill's.

## When to Use

- Maintainer asks for the issue triage queue, what's new, or about a specific issue.
- Maintainer asks about the issue graph, duplicates, orphaned milestone issues, or
  staleness.
- Not for: taking an action — that's `issue-actions`. Don't use `issue_write` here.

## Prerequisites

- `kyverno-context` loaded this session (live labels, and the `kyverno-doc:<path>`
  cache step 2's grounding reads from).
- `fetch_issue_candidates` (plugin tool, `plugins/kyverno-fetch/`) — one call fetches
  every candidate issue's full metadata, including `closing_prs` (every PR that
  references closing it, not just the one that merges), `closed_by`/
  `reopened_since_closed`, and `parent`/`sub_issues`/`tracked_in_issues` (GitHub's own
  relationship links, real when present). `detail="digest"` (capped at 60) drops
  body/comments for ranking/filtering a whole set by something search can't express
  directly (no linked PR, orphaned-milestone) — pair it with `fetch_issue_details` for
  just the chosen numbers' full data.
- `mcp-github`: `search_issues`, `list_issues`, `issue_read` (deep-dive only —
  `fetch_issue_candidates` covers the main queue), `search_pull_requests` (for revert-
  PR lookup), `search_code` (grounding an issue in the real code — step 2, required,
  not a fallback), `list_discussions`, `get_discussion`.
- `mcp-slack`: `conversations_history` — only if configured.
- `mnemosyne_recall` — `kyverno-doc:<path>` package docs for step 2's grounding (same
  cache `kyverno-context`/`pr-queue` already use), prior triage decisions, duplicate
  rulings, contributor patterns.
- Env: `KYVERNO_REPO`, `MAINTAINER_GITHUB_LOGIN`, `SLACK_HOME_CHANNEL`.

## Procedure: triage queue

**Step 0 — establish a focus before fetching.** If the request already names one
(a milestone, an author, an area, a date range, "the full backlog"), skip to
Step 1. Otherwise offer a handful of starting points — not an exhaustive list,
and the maintainer can name something else entirely instead of picking one:
the 10 newest triage-queue issues, the 10 oldest still in triage, the 10 oldest
with no linked PR, or the orphaned milestone issues (in a milestone, no linked
PR, no assignee). No fetch happens until something is picked.

1. **Fetch.** The label whose live `description` (`kyverno-context`) reads as the
   "still needs curating" default — usually named `triage` but confirm from its
   description, not the name — is always part of `search_query` (`is:open
   label:<that label>`) unless Step 0 picked the full backlog. Almost nothing is
   actually unlabelled: a kind label and that one both land on creation, so
   filtering for "unlabelled" finds nothing.
   - **A named focus, or a sort-based default** (newest/oldest): a direct search
     qualifier (`milestone:"..."`, `created:>DATE`, `sort:created-desc`/`-asc`)
     plus `limit=10` — one `fetch_issue_candidates(..., detail="full")` call.
   - **A relational default** (no linked PR, orphaned-milestone): neither is a
     search qualifier GitHub has. `fetch_issue_candidates(..., detail="digest",
     limit=60)` over the whole `label:triage` (or milestone) set, filter
     client-side for an empty `closing_prs` (plus no `assignees` for orphaned),
     take the oldest 10 by `created_at`, then `fetch_issue_details(repo,
     issue_numbers=[...])` for just those 10. State the slice size against the
     digest's own `total_count`, and against how many matched the filter before
     the cut to 10.
2. **Ground it in the real code, then classify.** A kind label (`bug`/`enhancement`)
   came from the issue form, not a maintainer's own judgment, and the body alone is
   one side of the story — understand what's actually on `main` before judging it:
   - Identify the likely subsystem/package from labels (`area/*`), file paths, symptoms,
     or error text in the body.
   - Ground it the same way a PR review brief already does: `mnemosyne_recall
     (kyverno-doc:<path>)` for that package's cached `AGENTS.md`/`ARCHITECTURE.md`, or
     `search_code` for the specific symbol/error/file the issue names if nothing's
     cached. This is required, not optional — classification from the body's claim
     alone, unchecked against the real code, is a guess.
   - Classify (bug/feature/question/duplicate/out-of-scope), assess real severity, and
     flag whether this reads as security-relevant — all from what the grounding
     actually showed, cited (the doc, or the code found), never a keyword guess. A
     security read gets stated plainly to the maintainer with the real `security`/
     `kind/security` label (`kyverno-context`'s live list) suggested — no separate
     disclosure channel, just tell the maintainer and let them decide.
3. **Duplicate candidates**: `search_issues` on the issue's distinctive terms. A real
   candidate gets a one-line comparison (what's the same, what differs), not just a
   number. `mnemosyne_recall` for an earlier duplicate ruling on the same pattern first.
4. **The issue↔PR relationship graph** — `closing_prs` already has every PR that
   references closing this issue. More than one is a normal outcome, not an edge case:
   a second contributor can open a competing PR without being assigned. Flag it as
   possible duplicate effort, not silently pick one.
5. **Progress synthesis, not a link list**: for each `closing_prs` entry, state how far
   along it is — `search_pull_requests`/`pull_request_read` for its current label/CI
   state (e.g. "PR #X addresses this, currently `needs-author-action`, CI green"). An
   issue with no `closing_prs` entry is stated plainly as unaddressed.
6. **Issue-to-issue relationships** — `parent`/`sub_issues`/`tracked_in_issues` (real
   GitHub links, when present) plus whatever the body itself says (the same reading
   pass step 2 already does covers this; there's no separate mechanical check). Rare
   use of the native fields isn't evidence of no relationship — the body is the more
   common source.
7. **Outside context** — same depth as `pr-queue` gives a PR, not a lighter pass:
   `SLACK_HOME_CHANNEL` if configured, and `list_discussions` (client-side matched, no
   search exists) for a thread naming this issue or its topic. Cite what was checked;
   empty means "not found there," not "never."
8. **Author-nudge recommendation**: if a `closing_prs` entry is `needs-author-action`
   and this issue reads as urgent — the real severity from step 2's grounding, an open
   release milestone, or both — recommend a nudge — raised here, executed by
   `issue-actions`.
9. **Raise-issue flag**: if step 7's Slack/Discussion pass turns up a real user-facing
   problem with no open issue tracking it, flag it here as a raise-issue candidate —
   `issue-actions` drafts and posts it on confirmation.

10. **After writing the answer, push to the dashboard** — items (each with its `closed-by`
    PRs in `related`), then the `triage` view, and the one-line note (`dashboard-sync`).

Completion criterion: every fetched issue appears exactly once and is on the dashboard, every claim traces to
a cited call or a fetched field, and a competing PR against the same issue is never
silently collapsed to one.

## Procedure: issue relationship surfacing (on request)

The full-sweep version of step 6 above — chains across the *whole* fetched set
(an issue blocking an issue blocking another), not just what one issue's own body
says on its own.

- **Orphaned milestone issues**: in `fetch_issue_candidates` for the milestone, no
  `closing_prs` entry and no `assignees`.
- **Reverted-closer issues (best-effort)**: for a `CLOSED` issue with `closed_by.pr_
  number` set, `search_pull_requests(query='is:merged "#<that PR number>"')` for a
  later merged PR titled or bodied as a revert of it. A hit means the issue is closed
  on GitHub but the underlying fix is gone — state this plainly, it's a real finding,
  not a guess; no hit doesn't prove it wasn't reverted some other way.
- **Cross-PR/open-issue overlap**: an issue in this queue whose subsystem guess matches
  an open PR's `changed_files` area, cited both ways.
- **Milestone health**: per milestone, open issue count, how many have a `closing_prs`
  entry, rough velocity from `createdAt`/`closedAt` spread.

## Procedure: staleness housekeeping (on request)

Fetch with `search_query` widened to recently-active/closed issues as needed:

- Issues carrying whichever label's live description (`kyverno-context`) means "more
  information requested" — not an assumed name — where `recent_comments`' last entry
  is from the original `author`, not a maintainer.
- Issues open a long time (`createdAt` old) with no `recent_comments` activity.
- Issues where a `recent_comments` entry reads like "fixed in vX" but `state` is still
  `OPEN`.

For each, draft the closing comment — `issue-actions` posts it on confirmation.

## Pitfalls

- Never call `issue_write`/`add_issue_comment` from here — classification and
  relationship-finding only, `issue-actions` executes.
- `closing_prs` can include a merged PR alongside an open competing one — state both,
  never collapse to "addressed."
- `search_issues` is natural-language only — don't rely on it for an exact state/label
  check; `fetch_issue_candidates`' own fields are the source of truth there.

## Verification

- An issue with two independent open PRs in `closing_prs` shows both, flagged as
  possible duplicate effort, with a progress synthesis for each.
- Slack/Discussions checked for an issue the same as `pr-queue` checks for a PR — not
  skipped or lightened.
- A `CLOSED` issue with a real revert PR against its closer is surfaced in relationship
  surfacing, cited by both PR numbers.
- Asked to close or label an issue directly: confirm this skill declines and names
  `issue-actions` instead, rather than calling `issue_write` itself.
- Pick an issue naming a specific package/symptom: confirm `mnemosyne_recall`/
  `search_code` actually runs before classification, and the classification cites
  the real doc or code found — not just a restatement of the issue's own claim.
- Pick an issue plausibly describing a vulnerability: confirm it's flagged plainly
  to the maintainer with the real security label suggested, grounded in what the
  code lookup showed — not a keyword match on the body.
