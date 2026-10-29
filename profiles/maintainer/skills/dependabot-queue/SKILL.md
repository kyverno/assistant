---
name: dependabot-queue
description: "Dependabot PR verdicts (merge now/fix first/your review/wait/close), cited and linked."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
---

# dependabot-queue Skill

Gives every open Dependabot PR exactly one verdict with timing. Split out of
`pr-queue` (same facts, same `fetch_pr_candidates`/`sequence_prs` tools) so each
skill loads only when actually asked for.

## When to Use

- "Which Dependabot PRs should I review/merge," "anything risky in the bumps,"
  "what do I do about #N" (a Dependabot PR).
- Not for: the human review queue — `pr-queue`. A sweep-labelled Dependabot PR
  (`ready-for-review`/`needs-review`) still shows up there too, with no special
  formatting — this skill is the dedicated, fuller pass, asked for by name.
- Not for: a stale/author-blocked Dependabot PR — `author-followup` (rare;
  Dependabot itself handles most of its own PRs' upkeep).

## Prerequisites

- `fetch_pr_candidates`/`fetch_pr_details` (plugin tools, `plugins/kyverno-fetch/`)
  — ranking here (security first, then `semver_level`) isn't search-expressible,
  so always digest-then-detail, never one flat fetch.
- `sequence_prs` (plugin tool, `plugins/kyverno-sequencer/`) — for step 6's
  blocks/blocked-by pass.
- `mcp-github`: `search_pull_requests`, `search_code`, `list_dependabot_alerts`,
  `get_dependabot_alert`, `list_releases`, `get_latest_release`,
  `pull_request_read`, `list_discussions`.
- `mcp-slack`: `conversations_history` — only if configured.
- `mnemosyne_recall`/`_triple_query` — see `kyverno-context`'s memory reference.
- Env: `KYVERNO_REPO`, `SLACK_HOME_CHANNEL`.

## Procedure: Dependabot queue

Every Dependabot PR appears exactly once.

1. **Fetch by author, not label.** Ranking (security first, then severity) is
   relational, not search-expressible: `fetch_pr_candidates(repo=KYVERNO_REPO,
   search_query="author:app/dependabot", detail="digest", limit=60)`, rank by
   security label/`GHSA-`/`CVE-` mention first, then `semver_level` (major/minor/
   patch, `unknown` treated as an unconfirmed major), take the top 10, then
   `fetch_pr_details(repo, pr_numbers=[...])` for those 10's full `bumps`/
   `copilot_review`/`ci_state`. State the slice size against the digest's own
   `total_count` — there may be more than 10 real Dependabot PRs open; say so
   rather than implying this is the whole set. The labels (`ready-for-review`,
   `needs-review`, `major-bump`) are the triage sweep's read of the same facts —
   cite them when they agree, and say so when a label is missing or contradicts
   the fetched level.
2. **Group the set**: the same dependency in several PRs (e.g. one action bumped in two
   directories) or one `group` is one decision, presented once. `labels` give the kind:
   `go` (module), `github_actions` (workflow action).
3. **Security**: a PR carrying `security`, or whose body names a `GHSA-`/`CVE-` ID,
   addresses a known advisory — cite the ID from the body. Also
   `list_dependabot_alerts(state="open")` once and match each alert's package to the
   bumps for its severity; a refused call means this token can't see alerts — say so
   and rely on the PR body.
4. **Risk, per PR**, each claim cited:
   - Go module: `search_code` the import path in `KYVERNO_REPO`, group call sites by
     package; hits under `pkg/engine`/`pkg/cel`/`pkg/webhooks`/`pkg/validation`/
     `pkg/image` are elevated risk. For a major or minor bump, read the release notes
     the PR body embeds and name any breaking change or deprecation that touches those
     call sites. `k8s.io/*`, `sigs.k8s.io/*` and `github.com/kyverno/api` bumps
     (`kind/codegen`) also need the regenerated outputs in the PR's `changed_files` — a
     `go.mod`/`go.sum`-only diff means they're stale.
   - Workflow action: `search_code` for `uses: <action>` to name the workflows that run
     it; one with write permissions, secrets, or a release/publish step is elevated.
     A major bump usually changes the action's runtime — read its notes for that.
   - CI not green: `pull_request_read(method="get_check_runs")` for the failing checks'
     names. The same check failing on unrelated Dependabot PRs is a shared cause —
     report it once and say it isn't this bump's doing.
   - `mnemosyne_triple_query` for `caused_e2e_failure` on the packages the call sites
     fall in; `mnemosyne_recall` for an earlier decision on this dependency (a
     deliberate skip, a known-bad version).
5. **Reviews**: `copilot_review.verdict`/`summary`/`findings`, and `coderabbit_approved`.
   When Copilot isn't approving, `pull_request_read(method="get_review_comments")` and
   say whether each finding needs a code change (blocking) or is advisory (changelog,
   docs). Any human review is read the same way.
6. **Relations**:
   - Came after: `search_pull_requests(query='author:app/dependabot is:merged "<dep>"')`
     for the earlier bump(s) of the same dependency or group — a PR opened right after
     its sibling merged is that follow-up, say which.
   - Related PRs: `search_pull_requests(query='is:open "<dep>"')` for non-Dependabot
     PRs that mention the dependency.
   - Blocks / blocked by: call `sequence_prs` with the Dependabot PRs plus any
     related open PRs. Dependabot PRs overlap on `go.mod`/`go.sum` by nature — report
     that as one note (merge one at a time; Dependabot rebases the rest), not as a
     conflict per pair. A `github.com/kyverno/api` bump orders ahead of PRs touching
     generated outputs; state `gate_blocked` wherever true.
   - Outside context: `SLACK_HOME_CHANNEL` if configured, and `list_discussions`
     (client-side matched, no search exists) for a thread naming the bot, a
     dependency, or a PR. Cite what was checked; empty means "not found there."
7. **Verdict and timing, per PR** — exactly one:
   - **Merge now**: patch/minor, `merge_state` CLEAN, CI green, Copilot approving, no
     elevated-risk call sites, no open question from step 6.
   - **Fix first**: a concrete, nameable blocker — a blocking Copilot finding,
     a conflict (`@dependabot rebase`), failing check, stale generated outputs.
   - **Your review**: major or unknown level, elevated risk, or a codegen-linked bump.
     Land it when you can watch main's post-merge conformance run (the only suite that
     exercises it), and never while `gate_blocked`.
   - **Wait**: CI pending, or blocked behind a sibling in step 6.
   - **Close/ignore**: a duplicate of another open PR, or a bump the maintainer
     already decided against (`@dependabot ignore this major version`).
   Executing any of these (approve, `@dependabot` or `@copilot` comment) is
   `pr-actions`; merging is always the maintainer's own click.
8. **Order**: security fixes first, by severity; then **Merge now**, oldest first
   (each `go.mod` merge makes the next need a rebase, so the batch goes one at a
   time); then **Fix first**; then **Your review**; then **Wait**; then **Close/
   ignore**. Outside context (step 6) and anything the maintainer said this session
   override the order.

Output: grouped under those verdict headings, each PR as its link, `dep from → to
(level)`, the verdict with its timing, and the cited reasons — risk (call sites by
file), Copilot's verdict, the CI cause, related PRs, and what it blocks or waits on.

## Pitfalls

- Don't assume a label exists — `kyverno-context`'s live `list_label` is the
  source of truth.
- Ranking by security/severity is relational — never a flat `limit`-only fetch;
  digest-then-detail every time, same reason `pr-queue`'s "most urgent" default
  needs it.

## Verification

- Ask "which Dependabot PRs should I merge": confirm the fetch is by author (an
  unlabelled Dependabot PR still appears), and every PR lands under exactly one verdict.
- Pick two PRs bumping the same dependency: confirm they're presented as one decision.
- Pick a major bump: confirm it lands under "Your review" with real call sites cited,
  never "Merge now".
- Pick a PR whose Copilot verdict isn't approving: confirm the findings are read and
  classed as blocking or advisory, not just the verdict name.
- Pick a batch of "Merge now" PRs all touching `go.mod`: confirm one note about
  sequential merging, not a conflict reported per pair.
