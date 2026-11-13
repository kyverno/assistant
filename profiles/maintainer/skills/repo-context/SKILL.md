---
name: repo-context
description: "What the repository and its organisation pack say: live labels, CODEOWNERS and docs, plus the pack's gates, file classes and facts."
version: 0.4.0
author: Suhaani Agarwal, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
---

# repo-context Skill

Two kinds of knowledge the queue and action skills depend on: the repository's live state (labels, CODEOWNERS,
milestones, docs, looked up fresh every session) and its **organisation pack** (the rules and facts that belong to
this organisation: branch gates, which files are generated or critical, which labels mean ready, its own prose
facts). The pack is data read by the `repo_pack` tool; nothing about one organisation is written in a skill. Does not
read PR content: that is `pr-queue`.

## When to Use

- Before filtering or scoring PRs by label, milestone or release priority.
- Before labelling anything (`pr-actions`), so the label exists.
- Before deciding whether `MAINTAINER_GITHUB_LOGIN` owns a changed path.
- Not for a PR's own diff, reviews or comments: `pr-queue` does that.

## Prerequisites

- `REPO_GITHUB_TOKEN` and `ASSISTANT_REPO` (`owner/repo`) from the profile's `.env`. The `fetch_*` tools default `repo` to
  it, so omit it.
- `repo_pack` (plugin tool, `plugins/repo-intel/`).
- `mcp-github` tools: `list_label`, `get_file_contents`, `get_team_members`, `get_teams`, `search_code`,
  `search_pull_requests`, `issue_read`.
- `mnemosyne_recall`, for cached `AGENTS.md` and `ARCHITECTURE.md` docs (step 4).

## The organisation pack

Call `repo_pack` once per session and keep the answer. It returns:

- `branch_gates`: a branch-wide merge gate, if the repository has one: its status name, the label on its failure
  issues, whether an issue's body names the branch, and the label that bypasses it. While a gate is open, every PR on
  the branch is blocked whatever it touches. No gates listed means there is no gate: never mention one.
- `file_classes`, `generated_inputs`, `dependency_bots`: which paths are generated, API surface, critical (elevated risk
  after merge) or test-only, which paths and modules feed generated files, and which dependency bumps need regenerated
  output. `sequence_prs` applies these; you explain them.
- `readiness`: the labels that mean "ready for a maintainer" (`ready_labels`) and "waiting on the author"
  (`attention_labels`), and checks that are infrastructure rather than the author's to fix.
- `milestones`, `approval_bots`, `review_bots`, `agent_docs`, `codeowners`.
- `facts`: the organisation's own prose (codegen, CI, label meanings, conventions). Treat it as the organisation's
  statement, dated, and re-check it against the live repository if a decision depends on it.

`pack: none` means the repository has no pack. Say so once and say what you therefore cannot tell (there are no file
classes, no gate, no known ready labels), then rely on the live repository. A pack error is returned as an error:
report it, do not guess around it. Labels and rules from another repository never apply here.

## Live-resolved every session

Never carry any of this across sessions or a repository change: this instance may be pointed at a different
repository, and a stale answer from one is wrong for another.

1. **Labels**: `list_label(owner, repo)`, held for the session. Never assume a label exists without seeing it here. Each
   label has a `description`: read that for what it means, never guess from the name. The pack's `ready_labels` and
   `attention_labels` say which ones the queue uses; if one is not in the live list, say so.
2. **CODEOWNERS**: `get_file_contents(path=<codeowners from the pack, default "CODEOWNERS">)`, parsed as ordered
   `(path-pattern, owner)` pairs. To find who owns a path, walk the lines in order and keep the *last* match (GitHub's
   rule is last match wins, not longest pattern). A team owner (`@org/team`) needs `get_team_members` to check whether
   `MAINTAINER_GITHUB_LOGIN` is a member.
3. **Milestone**: a real field on the PR, so `search_pull_requests(query='... milestone:"<title>"')` filters directly. The
   title format is `milestones.title_format` from the pack. The pack's `open_milestone_label`, if any, is a secondary
   label: do not treat it as equivalent to the PR having a milestone; check both independently.
4. **Agent docs**: the files the pack's `agent_docs` globs name (`AGENTS.md` and `ARCHITECTURE.md` at the root and per
   package, instructions files). `scripts/install.sh` caches the ones it finds into mnemosyne under `repo-doc:<path>`
   (for example `repo-doc:pkg/engine/AGENTS.md`). `mnemosyne_recall` the one for the package in play before falling back
   to `search_code(query="<terms> repo:${ASSISTANT_REPO}")`. The cache is a snapshot from install time: a doc added or
   changed later is not in it, so prefer the live file when a decision turns on its wording. If the repository has no
   such docs, say so: your knowledge of the code is then only what the code, owners and history show.
5. **Other repo docs on demand**: `search_code` for `CONTRIBUTING.md`, `docs/**` or a symbol's usages, scoped to the
   question, not preloaded.

## What this agent remembers, and where

Three places — `pr-queue`/`pr-actions`/`discussions` point here instead of repeating.

**MEMORY.md/USER.md** (built-in, ~800+500 chars) — fixed *slots*, replaced in place as
facts change, never appended to: USER.md holds review priority ordering, communication
style, delegation boundary, cadence, risk tolerance; MEMORY.md holds current
milestone/focus, standing holds, maintainer-stated sequencing intent, recently-settled
structural facts, active experiments, a pointer to Mnemosyne. **Never** write PR/
review/label state, Slack messages, per-PR incident/contributor history, or labels/
CODEOWNERS here — all of that is either always-live or belongs in Mnemosyne below.

**Mnemosyne** (`mnemosyne_*`, `memory.provider: mnemosyne`) — for what accumulates past
a slot's size or has no other live source: post-merge breakage per package-pair
(`triple_add(subject=<pair>, predicate="caused_gate_failure", object=<PR#, date>)`,
queried before flagging post-merge risk on a new PR touching the same packages),
rejected/deferred PR reasons (`remember`/`recall`), contributor patterns, durable
Slack-stated policies, milestone-scoped policy decisions
(`triple_add(subject=<milestone>, predicate="excludes"|"prioritizes", object=<policy>)`),
and the branch-gate incident timeline (`triple_add(subject=<branch>,
predicate="gate_failure_opened"|"gate_failure_closed", object=<date, issue#>)`). Only 8
tools: `remember`/`recall`/`update`/`forget`/`triple_add`/`triple_query`/`diagnose`/
`sleep`. **Real gap**: `write_approval: true` stages `remember` but `triple_add`
commits straight to the database — `hooks/block-mnemosyne-triples.sh` allowlists
exactly the five predicates above and blocks anything else; extending a use case means
extending that allowlist deliberately. Written interactively (contributor patterns,
Slack policies, milestone decisions, and any on-demand incident/rejection) or swept
(`cron/jobs.json`'s `memory-sweep`, for incidents/rejections/gate-timeline that
can happen with nobody asking; `memory-consolidate` runs `sleep` on a separate,
slower cadence).

## Pitfalls

- Don't carry labels/CODEOWNERS/milestone across sessions or a repository change.
- Last-match-wins for CODEOWNERS — a broader pattern overrides a narrower one if it
  comes later in the file.
- `search_issues` takes a natural-language query, not qualifier syntax — filter on the
  returned `milestone` field client-side. `milestone:"..."` is `search_pull_requests`-
  only.
- Don't preload docs the pack's globs don't name: `search_code` on demand.

## Verification

- Ask "what labels does `<repo>` have" for two different `ASSISTANT_REPO` values and
  confirm `list_label` runs each time, not a memorized list.
- Ask "does `<path>` need sign-off from `<team>`" and confirm a real
  `get_file_contents` + `get_team_members` call backs the answer.
- Ask for a milestone's PRs and confirm the pack's title format is used in `milestone:"..."` directly, no closing-issue detour.
- Ask about a `release-critical` issue with no milestone and confirm the answer
  doesn't invent one.
- Ask about a `workflow-approval-required` PR and confirm it states plainly that no
  tool here can approve the run.
- With a pack that lists no gate, confirm no answer mentions a gate; with no pack, confirm the answer says which signals
  are missing.
- Ask about `pkg/engine`'s conventions and confirm `mnemosyne_recall` for
  `repo-doc:pkg/engine/AGENTS.md` runs before any `search_code` fallback.
- Try to `triple_add` a predicate outside the five listed and confirm the hook blocks
  it.
