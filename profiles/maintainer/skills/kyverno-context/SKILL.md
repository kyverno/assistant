---
name: kyverno-context
description: "Live labels/CODEOWNERS/milestone lookup; Kyverno's codegen & CI facts."
version: 0.3.0
author: Suhaani Agarwal, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
---

# kyverno-context Skill

Two kinds of knowledge `pr-queue` and `pr-actions` both depend on: `KYVERNO_REPO`'s
actual live state (labels, CODEOWNERS, milestones, release priority — looked up fresh
every session, never assumed) and Kyverno's fixed codegen/CI facts (documented here,
not re-derived every time). Does not read PR content itself — that's `pr-queue`.

## When to Use

- Before filtering or scoring PRs by label/milestone/release-priority state.
- Before labeling a PR (`pr-actions`) — to use a label that actually exists.
- Before deciding whether `MAINTAINER_GITHUB_LOGIN` owns a changed path, via CODEOWNERS.
- Not for: reading a PR's own diff, reviews, or comments — `pr-queue` does that.

## Prerequisites

- `REPO_GITHUB_TOKEN` and `KYVERNO_REPO` (`owner/repo`) from the profile's `.env`. `fetch_*`
  tools default `repo` to `KYVERNO_REPO`, so omit it.
- `mcp-github` tools: `list_label`, `get_file_contents`, `get_team_members`,
  `get_teams`, `search_code`, `search_pull_requests`, `issue_read`.
- `mnemosyne_recall` — for the cached `AGENTS.md`/`ARCHITECTURE.md` docs (see
  "Live-resolved" step 4).

## Live-resolved every session

Never carry any of this across sessions or a `KYVERNO_REPO` change — this instance may
be repointed at a different repo, and a stale answer from one is actively wrong for
another.

1. **Labels**: `list_label(owner, repo)`, held for the rest of the session. Never
   assume a label exists without having seen it here. Each label carries its own
   real `description` — read that for what a label actually means, never guess from
   the name (e.g. `triage` describes itself as the default every new issue gets, not
   an absence of labelling; the label whose description reads like "further
   information requested" is the real "needs more info" marker, whatever it's named
   on a given repo).
2. **CODEOWNERS**: `get_file_contents(path="CODEOWNERS")`, parsed as ordered
   `(path-pattern, owner)` pairs. To find who owns a path, walk the lines in file
   order and keep the *last* match — GitHub's rule is last-match-wins, not
   longest-pattern. A team owner (`@org/team`) needs `get_team_members` to check
   whether `MAINTAINER_GITHUB_LOGIN` is actually a member.
3. **Milestone**: a real field on the PR itself today — `search_pull_requests(query='...
   milestone:"Kyverno Release X.Y.Z"')` filters directly. Naming convention:
   `"Kyverno Release X.Y.Z"` (e.g. `"Kyverno Release 1.20.0"`). `milestone-pr` is a
   secondary label (closes a milestone-tracked issue directly) — don't assume it's
   equivalent to the PR having a `milestone` set; check both independently.
4. **`AGENTS.md`/`ARCHITECTURE.md` docs**: `scripts/install.sh` caches every one of
   these in the repo (12 as of this writing: root `AGENTS.md` and `ARCHITECTURE.md`,
   `api/AGENTS.md`, and one per major package — `pkg/engine`, `pkg/cel`,
   `pkg/webhooks`, `pkg/controllers`, `pkg/clients`, `pkg/background`, `pkg/image`,
   `pkg/toggle`, `cmd/cli/kubectl-kyverno/exception`) into mnemosyne at install time,
   each under its own id, `kyverno-doc:<path>` (e.g. `kyverno-doc:pkg/engine/AGENTS.md`).
   `mnemosyne_recall` the one relevant to whatever package is in play before falling
   back to a fresh `search_code(query="<terms> repo:${KYVERNO_REPO}")` — the cache
   won't have a doc added after install, or anything outside these 12 paths.
5. **Other repo docs on demand**: `search_code` for `CONTRIBUTING.md`/`docs/**` or a
   symbol's usages — scoped to the current question, not preloaded.

## Fixed facts

Re-verify against the live repo (`.github/workflows/`, `.claude/settings.json`,
`api/AGENTS.md`, `gh label list`) if a decision materially depends on one of these and
it's been a while — they're frozen at the research pass that produced them, most
recently 2026-10.

**Codegen fan-out.** Two repos split the API types: `kyverno.io`/`wgpolicyk8s.io`/
`reports.kyverno.io` live in this repo's `api/**`; the CEL-based `policies.kyverno.io`
types (`ValidatingPolicy`, `MutatingPolicy`, etc.) live in the separate
`github.com/kyverno/api` Go module — a `go.mod`/`go.sum` bump of that module is the
generated-input event for that half, shaped like a Dependabot bump, not an `api/**`
diff. No-edit generated paths (from `.claude/settings.json`'s `permissions.deny`):
`zz_generated.*.go`, `/pkg/client/**`, `/pkg/clients/**/*.generated.go` (no `DO NOT
EDIT` header — path is the only signal; `pkg/clients/dclient/client.go` is the one
hand-written exception), `/config/crds/**`, `/cmd/cli/kubectl-kyverno/{config,data}/crds/**`
(the CLI's copy is second-order — updating `config/crds/**` without it is itself a
generated-file conflict), `/charts/**/crds/**` + `charts/**/README.md`,
`/docs/user/crd/**`, plus two single files (`pkg/config/mocks/mock_config.go`,
`config/install-latest-testing.yaml`). Outside `pkg/clients`, the reliable "is this
generated" signal is the `// Code generated ... DO NOT EDIT.` header, not just the
filename pattern. `check-codegen.yaml` gates all of this pre-merge — two PRs feeding
the same generated output conflict on it even when their own diffs don't overlap.

**Pre- vs. post-merge CI.** Pre-merge (`pull_request`-triggered, gates the PR):
`check-codegen`/`check-unit-tests`/`check-golangci-lint`/`check-vet`/`check-imports`/
`check-fmt`/`check-cli-tests`/`check-framework`/`check-ct-lint`/`check-ah-lint`/
`check-devcontainer`/`check-unused-package`/`check-sha-pinned-actions`. Post-merge only
(push-to-`main`/release-branch via `check-tests.yaml`, never on a PR):
`tests-conformance.yaml` (Chainsaw, 3 k8s versions), `tests-conformance-policy-library.yaml`
(12-way sharded, checks out the separate `kyverno/policies` repo and isn't scoped to
any package here), `tests-k6.yaml`, perf benchmarks. Nothing catches a bad interaction
between two individually-green PRs before both land — that's what `e2e-gate` reacts to
below. `pr-queue` treats a PR touching core policy-evaluation surface (`pkg/engine`,
`pkg/cel`, `pkg/validation`, `pkg/image`, `pkg/webhooks`) as elevated post-merge risk
on this basis — there's no precise package→suite table to build, since the policy-
library suite genuinely isn't package-scoped.

**`e2e-gate` — branch-level, not path-level.** When the post-merge conformance suite
fails on a branch, an `e2e-failure`-labelled issue opens, and a required "E2E Gate"
commit status turns **red on every open PR targeting that branch** — unconditionally,
regardless of which files any individual PR touches. The only escape is the PR itself
carrying `e2e-gate-bypass`. Each issue's body carries its branch as
`<!-- workflow-failure:...:refs/heads/BRANCH -->`; no marker found blocks *every*
branch. No mechanical "safe because it doesn't touch the failing files"
path exists — `sequence_prs` states `gate_blocked` as a fact; any bypass suggestion
is the agent reading the failure issue itself, never automatic. `ready-for-review`
is independent of this — a PR can carry both.

**Label taxonomy** (from `.github/labels.yml` — step 1 above re-checks the full list
every session, this is reference):

- `ready-for-review`: DCO/CI/conflicts/review-threads all clean — shared by human and
  Dependabot PRs alike; a clean Dependabot bump gets this same label, not a separate
  one.
- `needs-author-action`: the human-PR finding (failing DCO/CI, a merge conflict, or
  an unresolved thread).
- `needs-review`: the Dependabot equivalent of `needs-author-action`, not of
  `ready-for-review` — a major/unconfirmed-semver bump, failing CI, a conflict, or
  Copilot not recommending approval. Always name the actual cause, never just the
  label.
- `workflow-approval-required`: a fork PR awaiting a maintainer's manual approval of
  its Actions run before CI starts — distinct from, and can co-occur with,
  `needs-author-action`. No tool in this toolset can approve a pending run
  (`config.yaml`'s granted `mcp-github` tools are read-only) — say so, point at the
  GitHub UI.
- `milestone-pr`: closes an issue under a currently **open** `"Kyverno Release ..."`
  milestone — narrower than just having `milestone` set (step 3 above).
- `major-bump`: permanent, paired with `needs-review` — an unconfirmed-or-major
  Dependabot bump.
- `stale` / `no-stale`: `stale` is a daily workflow's mark on a `needs-author-action` PR
  whose author hasn't pushed a commit in 14 days (never closes anything; Dependabot and
  draft PRs are skipped). `no-stale` is a maintainer's exemption from it.
- `ai-generated` / `spam`: CodeRabbit slop/spam-burst flags, not sequencing input.
- `release-critical`/`-high`/`-medium`/`-low`: not in `.github/labels.yml` — no
  workflow applies or removes them. Still real: maintainers set these by hand on an
  **issue** to mark its urgency toward a milestone. Check a PR's closing issue's
  labels (`fetch_pr_candidates`'s `closing_issues[].labels`) for these, not the PR's
  own labels.

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
(`triple_add(subject=<pair>, predicate="caused_e2e_failure", object=<PR#, date>)`,
queried before flagging post-merge risk on a new PR touching the same packages),
rejected/deferred PR reasons (`remember`/`recall`), contributor patterns, durable
Slack-stated policies, milestone-scoped policy decisions
(`triple_add(subject=<milestone>, predicate="excludes"|"prioritizes", object=<policy>)`),
and the `e2e-gate` incident timeline (`triple_add(subject=<branch>,
predicate="e2e_failure_opened"|"e2e_failure_closed", object=<date, issue#>)`). Only 8
tools: `remember`/`recall`/`update`/`forget`/`triple_add`/`triple_query`/`diagnose`/
`sleep`. **Real gap**: `write_approval: true` stages `remember` but `triple_add`
commits straight to the database — `hooks/block-mnemosyne-triples.sh` allowlists
exactly the five predicates above and blocks anything else; extending a use case means
extending that allowlist deliberately. Written interactively (contributor patterns,
Slack policies, milestone decisions, and any on-demand incident/rejection) or swept
(`cron/jobs.json`'s `kyverno-memory-sweep`, for incidents/rejections/gate-timeline that
can happen with nobody asking; `kyverno-memory-consolidate` runs `sleep` on a separate,
slower cadence).

## Pitfalls

- Don't carry labels/CODEOWNERS/milestone across sessions or a `KYVERNO_REPO` change.
- Last-match-wins for CODEOWNERS — a broader pattern overrides a narrower one if it
  comes later in the file.
- `search_issues` takes a natural-language query, not qualifier syntax — filter on the
  returned `milestone` field client-side. `milestone:"..."` is `search_pull_requests`-
  only.
- Don't preload docs outside the 12 cached paths — `search_code` on demand.

## Verification

- Ask "what labels does `<repo>` have" for two different `KYVERNO_REPO` values and
  confirm `list_label` runs each time, not a memorized list.
- Ask "does `<path>` need sign-off from `<team>`" and confirm a real
  `get_file_contents` + `get_team_members` call backs the answer.
- Ask for a milestone's PRs and confirm `milestone:"Kyverno Release X.Y.Z"` is used
  directly, no closing-issue detour.
- Ask about a `release-critical` issue with no milestone and confirm the answer
  doesn't invent one.
- Ask about a `workflow-approval-required` PR and confirm it states plainly that no
  tool here can approve the run.
- Ask about `pkg/engine`'s conventions and confirm `mnemosyne_recall` for
  `kyverno-doc:pkg/engine/AGENTS.md` runs before any `search_code` fallback.
- Try to `triple_add` a predicate outside the five listed and confirm the hook blocks
  it.
