---
name: pr-queue
description: "Merge-sequence recommendations and per-PR review briefs, cited and linked."
version: 0.3.0
author: Suhaani Agarwal, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
---

# pr-queue Skill

Builds an ordered merge-sequence recommendation and answers open-ended questions about
the queue or any individual PR. Relies on `kyverno-context` for labels/CODEOWNERS/
milestone/fixed facts — assumed loaded, not re-derived here.

## When to Use

- Maintainer asks for their review queue, what's outstanding, or about a specific PR.
- Maintainer reports CI broke on `main` and wants to know what's affected.
- Not for: taking an action (label, comment, approve, rebase) — that's `pr-actions`.
  Not for: a dedicated Dependabot pass — `dependabot-queue` (a sweep-labelled
  Dependabot PR still appears in this queue too, with no special formatting). Not
  for: a stale/author-blocked PR — `author-followup`.

## Prerequisites

- `kyverno-context` loaded this session.
- `fetch_pr_candidates` (plugin tool, `plugins/kyverno-fetch/`) — one call fetches
  every candidate's full metadata. `detail="digest"` (capped at 60) drops body/
  review text for ranking a whole set by something search can't filter on
  directly ("most urgent") — pair it with `fetch_pr_details` for just the
  chosen numbers' full data.
- `sequence_prs` (plugin tool, `plugins/kyverno-sequencer/`) — builds the hard-
  dependency graph and layers it into tiers.
- `fetch_file_diff_overlap` (plugin tool, `plugins/kyverno-fetch/`) — fetches
  the real diff hunks for a `file_overlaps` entry's shared file, to read.
- `mcp-github`: `search_pull_requests`, `search_issues`, `list_issues`, `issue_read`, `search_code`, `list_releases`,
  `get_latest_release`, `list_code_scanning_alerts`, `get_code_scanning_alert`,
  `list_secret_scanning_alerts`, `get_secret_scanning_alert`, `actions_list`,
  `actions_get`, `get_job_logs`, `request_copilot_review`, `pull_request_read`
  (review-brief deep dives only — `fetch_pr_candidates` already covers the main
  queue build), `list_discussions`, `get_discussion` (read-only context lookup —
  posting a reply is `discussions`' job, not this skill's).
- `mcp-slack`: `conversations_history`, `conversations_add_message` — only if
  configured.
- `mnemosyne_recall`/`_triple_query` (read), `mnemosyne_remember`/`_triple_add`
  (write) — see `kyverno-context`'s memory reference.
- Env: `KYVERNO_REPO`, `MAINTAINER_GITHUB_LOGIN`, `SLACK_HOME_CHANNEL`.

## Procedure: build the merge-sequence recommendation

**Step 0 — establish a focus before fetching.** If the request already names one,
skip to Step 1. Otherwise offer a handful of starting points — not an exhaustive
list, and the maintainer can name something else instead: a milestone, an author,
an area (package/folder), `workflow-approval-required` PRs instead of the review
queue, or the 10 most urgent `ready-for-review` PRs. Never default to
oldest-first or "all PRs." Exception: `cron/jobs.json`'s `kyverno-review-digest` has
no one to ask — use a fixed fallback (highest-urgency closing-issue label, then
`coderabbit_approved` + zero unresolved threads, then most-recently-updated) and
label it as the digest default, not the general rule.

"Most urgent 10" ranks by the same ladder step 4 uses (closing-issue severity,
milestone proximity, `coderabbit_approved`, author association, size) — none of
that is a search qualifier, so it needs the digest pass: `fetch_pr_candidates(
search_query="label:ready-for-review", detail="digest", limit=60)`, rank
client-side, take the top 10, then `fetch_pr_details(repo, pr_numbers=[...])`
for just those 10. State the slice size against the digest's own `total_count`.

1. **Fetch**: `fetch_pr_candidates(repo=KYVERNO_REPO, search_query="label:ready-for-review")`
   plus the same call with `label:needs-review` — or one call with
   `"label:ready-for-review,needs-review"` if the tool's search syntax supports an
   OR (check its schema). Add whatever Step 0 established as its own qualifier
   (`milestone:"..."`, `author:...`) rather than post-filtering. An area/package
   focus is a client-side filter on the returned `changed_files` instead, since
   GitHub search has no path qualifier. State the slice size vs. `total_count`
   whenever the set is narrowed. If Step 0 picked `workflow-approval-required`,
   fetch that separately and present it as its own short list (Output format below)
   — it never goes into `sequence_prs`. For each, `actions_list` on the PR's head
   branch/SHA to find the run waiting on approval, then cite that run's own
   `html_url` — a real link to the approval page, not a generic pointer at GitHub.
   Dependabot PRs the triage sweep hasn't reached yet carry neither label and aren't
   in this set — `dependabot-queue` fetches by author instead.
2. **Precedence**: nothing to prepare — `sequence_prs` derives all four hard-edge
   types (stacked, generated-file, explicit body reference, closing-issue conflict)
   itself from the fetched `body`/`changed_files`/`closing_issues`/`dependency_bumps`.
   `fetch_pr_candidates` already resolved any body reference pointing outside the
   fetched set (`external_references`: its real kind/state/title) — cite that
   directly per PR, never as a "please go check" item.
3. **Gate check**: `list_issues(owner=..., repo=..., state="open", labels=["e2e-failure"])`,
   every time, cited — never assert "no gate open" without this call. `search_issues`
   is natural-language only and can miss a real open issue; don't use it here. Pass
   each open issue's branch marker (parsed from its body, or `null` if it has none)
   as `blocking_issue_branches`.
4. **Call `sequence_prs`** with every candidate's `number`/`changed_files`/`labels`/
   `base_branch`/`head_branch`/`body`/`closing_issues` (as fetched)/`dependency_bumps`/`files_truncated`,
   `repo_default_branch`, and `blocking_issue_branches`. It returns `tiers` (each PR
   tagged with `hard_constraints`, `rebase_flag`, `gate_blocked`, `file_classification`),
   `cycles`, `unresolved`, `file_overlaps`, and `package_overlaps` — a structural
   report, not an order:
   - `cycles`/`unresolved`: hand those PRs to the maintainer by number; don't pick an
     order for them.
   - `file_overlaps` (same file, no hard edge): call `fetch_file_diff_overlap
     (repo, path, pr_numbers)` for it before presenting — it returns each PR's
     raw patch for that file, no computed verdict. There's no reliable way to
     mechanically tell whether two PRs' edits will textually conflict, so
     don't claim one — read both patches and describe what's actually there:
     same function, same behavior, contradicting assumptions, or genuinely
     unrelated. Whichever PR merges second needs the normal rebase-onto-
     latest-main step regardless, since the file did change either way. This
     also doesn't catch two PRs touching *different* files that depend on
     each other (a changed function signature here, an unchanged caller
     there) — say so plainly if asked; nothing here checks that.
   - `package_overlaps` (same directory, different files — weaker, can't git-
     conflict): cite the pair and path as a "review together" note, not a warning
     on par with a real file overlap.
   - **Historical risk**: for any PR (or overlapping pair) classified
     `ADMISSION_CRITICAL`, or named in a `file_overlaps`/`package_overlaps`
     entry, `mnemosyne_triple_query` for a `caused_e2e_failure` predicate on
     that package. A real past incident (PR#, date) is a specific, citable
     post-merge-risk finding — stronger than the generic "core engine
     surface" warning; say plainly when the query returns nothing rather
     than skipping the check.
   - **Within-tier order, in two passes — cluster, then rank.** No hard
     dependency exists between any two PRs in the same tier, but that
     doesn't mean they're unrelated.
     1. **Cluster first.** Any PRs connected by a `file_overlaps`,
        `package_overlaps`, or `unresolved` entry (same file/directory, or
        both close the same issue) stay adjacent in the final order and get
        presented as one group with one shared note — never scattered apart
        and re-explained separately (e.g. the real case of two same-author
        CLI fixes sharing a file: present and sequence them as a pair, not
        as independent positions three slots apart).
     2. **Rank clusters and standalone PRs by a strict ladder** — each level
        only breaks ties *within* the level above it; it never promotes a
        PR past one that ranked higher on an earlier level:
        a. **Severity of the closing issue** — assign from its real content,
           never a guess: `CRITICAL` (crash/outage/data-loss, or
           `kind/security`) > `HIGH` (a `regression` label, or a user-facing
           correctness break) > `MEDIUM` (an ordinary `bug`, or tech debt) >
           `LOW` (`enhancement`, `kind/tests`, `kind/documentation`, chore).
           `CRITICAL` outranks every `HIGH` regardless of anything below.
        b. `release-critical`/`-high`/`-medium`/`-low` on the closing issue
           (not the PR's own labels — see `kyverno-context`), then
           `milestone_due_on` proximity.
        c. `coderabbit_approved` + zero unresolved threads.
        d. `author_association` (`MEMBER` > `COLLABORATOR` > `CONTRIBUTOR` >
           `FIRST_TIME_CONTRIBUTOR`/`NONE`).
        e. `size` — smaller first, a pure tiebreak, never a priority driver.
     Slack context (step 6) and anything the maintainer said this session
     override every level. State the reason as the judgment it produced
     ("CRITICAL — admission controller crash"), not the raw field.
   - **Readiness is a separate axis from priority — never conflate them.**
     A PR with CodeRabbit `CHANGES_REQUESTED`, a real CI failure, or no
     review yet can still rank high by severity, but never goes in an
     "approve now"-style grouping — name the blocker plainly there instead.
     `size` also calibrates review effort independently of rank — call out
     a large PR as needing a dedicated session regardless of where it lands.
   - `gate_blocked`: state plainly wherever true — merging is blocked branch-wide
     regardless of files touched; review work isn't. If the failure issue's content
     (job/test names) suggests this PR is unrelated, say so as your own read and
     suggest `e2e-gate-bypass` to the maintainer — never imply it's already safe,
     never apply it yourself.
5. **Diagnose every `needs-review` entry** — `pull_request_read(method="get_reviews")`
   for Copilot's verdict, `get_check_runs`/`get_status`/`get`'s mergeable-state for a
   conflict. Name the actual cause ("Copilot flagged the changelog note as missing,"
   not "not approved yet"). For `major-bump`, pull the module's own `list_releases`
   and `search_code` its real call sites in `KYVERNO_REPO` — the actual blast radius.
   A `github.com/kyverno/api` bump additionally needs: (a) `search_code` for
   `policies.kyverno.io` type/alias usage in hand-written `pkg/**` code; (b) whether
   this PR's own diff already includes the matching regenerated outputs — a
   `go.mod`/`go.sum`-only diff means they're now stale, a citable finding.
6. **Check for outside context** — `SLACK_HOME_CHANNEL` if configured, and
   `list_discussions` always (client-side matched, no search exists) — for a
   thread naming the bot, a PR, or its topic. Cite and apply as in step 4's
   within-tier rule.
7. **Produce one ordered list**, tier by tier, then push it to the dashboard
   (`dashboard-sync`: items, then the `queue` view) and close with its one-line note. Every position — human or Dependabot —
   renders identically (Output format below): no separate section, no bolded
   verdict styling that singles one kind out.
8. **Offer the author-blocked check for this same scope** — `fetch_pr_candidates(...,
   search_query="label:stale")` with Step 0's scope added, count-only (no
   `include_stale_detail`, that's `author-followup`'s own fetch). A nonzero count: name
   it and offer to look into them (`author-followup`), don't run it unasked. Zero: skip
   silently, don't mention it.
9. **Offer a guided walkthrough**: work the queue in the order just produced, one PR
   at a time (brief → decide → act → next — the review-brief procedure's own
   execute-offer below), rather than only handing back the static list. Maintainer
   can accept, or jump to any PR by number instead — never insist on the walkthrough
   once a number is named.

Completion criterion: every fetched candidate appears exactly once, every claim
traces to a cited tool call or `sequence_prs` field, and a human could re-derive the
order from the stated reasons alone.

## Procedure: review brief (explain PR #N)

Resolves live every time — no cache across a conversation. Short form (default):
what it changes (from the fetched `body`/`changed_files`, or `pull_request_read
(method="get_diff")` if asked for the actual diff), why (its `closing_issues`, their
milestone/labels), and existing review output (CodeRabbit for security/lint/tests/
codegen, Copilot for logic/cross-file impact — say plainly if neither has reviewed it
yet).

Long form (asked for, or a signal below is concerning) adds:

- Review-thread state: `pull_request_read(method="get_review_comments")`'s
  `is_resolved` + last commenter per thread — name which threads, not just a count.
- Risk: the same file-classification/gate check as the main Procedure, for this PR.
- Slack/Discussions context: `conversations_history(limit="7d")` if Slack is
  configured (widen on request), and `list_discussions`, both matched
  client-side against the PR's number/title/topic — no search exists for
  either. State what was checked; empty means "not found there," not "never."
- Suggested action (approve / request changes / wait on CI / needs author to resolve
  threads / proceed but flagged as high post-merge risk), stated with its reason.
  Before drafting, `mnemosyne_recall` for durable notes on this PR's author.

After writing the brief, push this PR's item to the dashboard (`dashboard-sync`).

**Offer to execute the suggestion in the same turn** — "want me to approve this
now?" not a separate ask the maintainer has to come back for. Short-form offers
just that one action. Long-form (deep-dive, or a risk signal already made this
long-form) widens the offer to whichever of `pr-actions`' full menu actually fits
here — approve, request changes, comment, a label, a Discussion/issue nudge, or
handing a stalled author's case to `author-followup` — never every action
regardless of fit.

## Answering open-ended questions

Look with the tools above, cite what was found — never a general prior about a
"typical" Kyverno PR. **"Is anything else related to this?"** — `search_issues` for
currently-open mentions, `list_discussions` for a related thread, plus
`mnemosyne_recall` for closed/rejected history with a reason. **"CI broke on
`main`"** — real input, not polled for; `actions_list`/
`actions_get`/`get_job_logs` on demand, then cross-reference against the current
queue's file overlap, naming specific PRs and paths.

## Output format

- Every PR or issue mentioned gets a real link:
  `https://github.com/${KYVERNO_REPO}/pull/<n>` or `/issues/<n>` — not a bare `#n`.
- Every candidate uses the same per-position shape regardless of label or author:
  link, what it is, the cited reason(s), any `warnings`. No position gets special
  headings or verdict styling another doesn't.
- State a *conclusion*, not a label dump — the maintainer can already see labels
  on GitHub. Say what to do (review this first, approve, land together, wait on
  X), what relates to what, and what's likely to break — the synthesis is the
  value, not a restatement of fetched fields.
- Explain each PR from what it actually does — not a paraphrase of its title.
  When it touches a package with a cached `kyverno-doc`, recall it and ground
  the explanation in that package's real purpose. When several PRs in the
  same answer serve the same underlying effort (same milestone, same tracking
  issue, same area), say so instead of listing them as unconnected items.
- Cite any `external_references` by what they resolved to ("Parent: #17701, an
  open tracking issue — nothing to wait on"), never as a to-do for the maintainer
  to check themselves.
- State the slice size vs. the real total whenever the set was narrowed.

## Pitfalls

- Don't assume a label exists — `kyverno-context`'s live `list_label` is the source
  of truth.
- Don't recompute the readiness workflows' own gating — use the label as the filter.
- Don't poll or loop checking post-merge CI or the gate — one on-demand call, never a
  watch.
- Never report a `needs-review` entry with just the label name — step 5's diagnosis
  runs every time.
- Don't default to oldest-first.

## Verification

- Ask "what's my PR queue" with no other context: confirm Step 0 fires before any
  fetch. Ask again naming a milestone: confirm it fetches directly, no question.
- Build a queue with both human and Dependabot `needs-review` entries: confirm
  identical formatting.
- With an open `e2e-failure` issue: confirm `gate_blocked` is stated for every
  non-bypass PR on that branch, and any bypass read is the agent's own, not
  mechanical.
- Confirm two candidates in the same tier, differing only in `coderabbit_approved`,
  get that stated as the tiebreak.
- Ask about a PR with a related GitHub Discussion: confirm `list_discussions` is
  checked and the thread is cited.
- Construct a case with a `CRITICAL`-severity PR (e.g. a crash) and a `HIGH`-severity
  one with a closer milestone due date: confirm the `CRITICAL` one still ranks
  first — severity isn't overridden by a lower level.
- Pick a real `file_overlaps`/`package_overlaps` pair: confirm they're presented
  adjacent as one group, not scattered to separate positions with the connection
  re-explained.
- Pick a PR with CodeRabbit `CHANGES_REQUESTED`: confirm it never lands in an
  "approve now"-style grouping even if its severity ranks it high.
- Confirm a closing issue's `release-critical` label is cited when present, and that
  the PR's own labels are never checked for it.
- Confirm every PR/issue number in a real answer is a working link.
- Ask about `workflow-approval-required` PRs: confirm they're named separately,
  each with its real approval-page link from `actions_list`, and that posting the
  approval itself is still named as the maintainer's own, in-browser action.
- Re-run the same queue twice with no repo-state change: confirm stable output.
- Pick two PRs touching the same `api/**` path: confirm the generated-file conflict
  is called out explicitly.
- Pick a real `file_overlaps` pair: confirm `fetch_file_diff_overlap` runs before
  presenting, and the answer reads the actual patches to describe what's
  colliding — never a bare "lines overlap/don't overlap" verdict.
- Confirm a `package_overlaps` pair (same directory, different files) reads as a
  softer "review together" note, never phrased like a confirmed conflict.
- Confirm a `FIRST_TIME_CONTRIBUTOR` PR gets cited as such somewhere it matters
  (ordering or the explanation), without becoming the sole reason to deprioritize
  it.
- Ask about a PR touching `ADMISSION_CRITICAL` surface: confirm `mnemosyne_triple_query`
  runs for `caused_e2e_failure` on that package, with the result stated either way
  (a real incident cited, or "nothing found").
- Pick a PR whose milestone is due soon vs. one with no milestone: confirm
  `milestone_due_on` is cited as the reason the first ranks earlier.
- Pick a large PR (many files/additions) vs. a tiny one: confirm `size` is named
  as a review-effort signal, not silently ignored.
