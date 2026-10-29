# Architecture

> **In the sandbox deployment** the agent runs inside an OpenShell sandbox, which adds a network proxy that swaps in the GitHub token, refuses merges, repository deletion and GraphQL mutations on the REST API, and checks every GitHub MCP tool call against the same allowlist as `config.yaml`. See [`container.md`](container.md) for those extra layers. This document describes the design and the layers common to both setups.

kyverno-assistant is a [Hermes profile distribution](https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions):
`distribution.yaml` + `SOUL.md` + `config.yaml` + `skills/` + `plugins/` + `hooks/` +
`cron/`, git-installed with `hermes profile install`, updated with `hermes profile update`.

- A single, on-demand agent process talking to one maintainer over Slack/CLI — not a
  webhook-triggered service. Human-invoked only, by design (see `SOUL.md`); triggered or
  autonomous operation is out of scope, not an oversight.
- MCP servers (GitHub, Slack) are declared under `config.yaml`'s `mcp_servers:` key directly
  — no separate `mcp.json`. A standalone `mcp.json` is only an *import* format
  (`hermes import-agent claude-code`), not something a profile install reads.

## What it can and can't do

- Reads anything on `KYVERNO_REPO`: PRs, diffs, reviews, labels, CODEOWNERS, milestones,
  issues, Discussions, security/Dependabot alerts, Actions logs. Reads the configured Slack
  channel.
- Using the maintainer's own `REPO_GITHUB_TOKEN`: adds labels, posts comments, requests changes,
  approves reviews, updates a PR branch against its base, replies to Discussions.
- **Cannot merge a PR.** No merge tool exists anywhere in the toolset — enforced in three
  independent layers (below), not a prompt instruction. See `docs/workflow.md`'s "What the
  agent cannot do" for the full, current action boundary list.
- Cannot re-trigger or detect post-merge CI on its own — no webhook receiver, no polling.
  If the maintainer mentions CI broke on `main`, `pr-queue` can take that as input and check
  file overlap against the current queue, but this is conversational, not monitored.

## Toolset allowlist — three independent layers

1. **`mcp_servers.github.headers.X-MCP-Tools`** — the allowlist GitHub's hosted MCP server
   (`https://api.githubcopilot.com/mcp/`) applies itself: tools not named do not exist for the
   connection, whatever the toolset. It must list exactly the tools under `tools.include`;
   `scripts/install.sh` fails when the two differ.
2. **`mcp_servers.github.tools.include`** — Hermes' own per-tool filter, matched against raw
   (unprefixed) tool names.
3. **`hooks.pre_tool_call` → `profiles/maintainer/hooks/block-dangerous-tools.sh`** — a shell hook, `matcher`-
   scoped to any tool name containing `merge`, `delete_repo` or `force_push` (the matcher is a
   full-match regex, so MCP names like `mcp__github__merge_pull_request` need the `.*` wrapper),
   `fail_closed: true` (shell hooks fail open by default; this is inverted on purpose for a security gate). Reads the tool-call event as
   JSON from stdin — Hermes pipes the event that way, not via an env var. Every invocation
   that matches the `matcher` is blocked; there's no conditional branch inside the hook.

`profiles/maintainer/hooks/block-mnemosyne-triples.sh` is the same pattern, scoped to `mnemosyne_triple_add`,
restricting it to the exact 5 predicates this distribution writes (`memory.write_approval`
stages `mnemosyne_remember` correctly but doesn't gate `triple_add`, which commits straight to
the database — this hook is the real control for that one tool).

Slack is narrower by design — only `conversations_history`/`conversations_add_message` are
ever wanted:
- `SLACK_MCP_ENABLED_TOOLS` must list both tools explicitly, even though only one writes —
  tool *registration* and the per-call channel restriction are independent checks in the
  server; omitting a tool from this list means it never registers at all, regardless of the
  channel scoping.
- `SLACK_MCP_ADD_MESSAGE_TOOL=${SLACK_HOME_CHANNEL}` is the actual write-channel restriction,
  enforced at call time, independent of the list above.
- The server fetches all four Slack channel types (`public_channel`/`private_channel`/`mpim`/
  `im`) in one call at boot and exits fatally if the token is missing a scope for any one of
  them — so the bot token needs `channels:read`/`groups:read`/`mpim:read`/`im:read` even
  though this profile only reads one public channel.
- `conversations_history` takes a time/count `limit`, no search query — "did anyone mention
  this PR in Slack" is a bounded-window scan with client-side text matching, never a real
  search (Slack's own `search.messages` API doesn't work with a bot token anyway).

## Why two bundled plugins instead of MCP servers

`profiles/maintainer/plugins/kyverno-fetch/` and `profiles/maintainer/plugins/kyverno-sequencer/` are bundled Hermes plugins (plain
Python, not MCP servers) — `__init__.py` exposes `register(ctx)`, which registers each
tool's schema (from `tools.py`) with Hermes; still needs an explicit `config.yaml` toolset
grant and a `plugins.enabled:` entry like any other tool — bundled isn't auto-trusted. They
ship with the repo (no separate packaging step, no extra install) and are discovered
per-profile under `~/.hermes/profiles/<profile>/plugins/<name>/`.

- **`kyverno-fetch`** — `fetch_pr_candidates(repo, search_query, limit)` replaces what would
  otherwise be a `search_pull_requests` call plus one sequential `pull_request_read` per PR
  (real cost against a live queue: dozens of PRs, one round trip each). It talks to GitHub's
  GraphQL/REST APIs directly over `urllib` using the profile's own `REPO_GITHUB_TOKEN` — same
  scopes already granted to the `github` MCP server, no new credential — and does its search
  pass plus a concurrent (`ThreadPoolExecutor`, max 8) per-PR detail pass inside one tool
  call. Returns per PR: labels, milestone, `author_association`, size, CI state (the PR's own
  checks, with the "E2E Gate" status excluded — see below), CodeRabbit approval,
  `closing_issues` (each with its own milestone/labels/open-state), and `external_references`
  (a body reference like "Depends on #N" resolved to its real kind/state/title, since issues
  and PRs share one GitHub number sequence). `fetch_file_diff_overlap(repo, path, pr_numbers)`
  fetches each PR's real diff for one file on demand — raw patches only, no computed
  conflict verdict (two PRs' hunk line numbers are relative to different merge-bases with
  `main`, so comparing them numerically isn't reliable; the agent reads the content itself).
  `fetch_issue_candidates(repo, search_query, limit)` does the same shape for issues:
  a search pass plus a concurrent per-issue detail pass. Returns per issue: labels,
  assignees, milestone, `comment_count`/`recent_comments`, `closing_prs` (every PR whose
  body/commits name a closing keyword for this issue, via GitHub's own
  `closedByPullRequestsReferences` — resolves multiple competing PRs against one issue
  without a body-text regex), and `closed_by`/`reopened_since_closed` (the timeline's
  actual closer — PR number or commit — and whether a reopen followed, from
  `timelineItems`). Verified live against `kyverno/kyverno` (2026-10-05): both
  `closedByPullRequestsReferences` and `ClosedEvent.closer`'s `PullRequest`/`Commit`
  union resolve correctly end to end, not just by schema introspection.
- **`kyverno-sequencer`** — `sequence_prs` takes that richer metadata and builds a hard
  dependency graph, no scoring: 4 edge types (stacked branches — one PR's base branch is
  another's head; generated-file input-before-output; an explicit body reference to another
  candidate; two candidates closing the same issue, which goes to `unresolved` instead of an
  edge), cycle detection (Kahn's algorithm), and topological layering into **tiers** by
  repeated Kahn's-algorithm peeling. Within a tier, no hard edge exists between any member —
  the tool returns them flat and unordered; ordering within a tier is the agent's judgment
  call (session context, Slack/Discussions, maintainer history), made explicit in
  `profiles/maintainer/skills/pr-queue/SKILL.md`'s precedence ladder, not something the tool decides. Also
  computes per-PR annotations: file classification (`GENERATED` > `API_SURFACE` >
  `ADMISSION_CRITICAL` > `TEST_ONLY` > `STANDARD`), `rebase_flag` (shares a changed file with
  an earlier tier), `file_overlaps`/`package_overlaps` (no hard edge), `gate_blocked`
  (branch-level e2e-gate status, independent of `ready-for-review`).

## Why codegen and CI structure matter for sequencing

From `kyverno/kyverno`'s own `Makefile` and `.github/workflows/`:

- Anything under `api/**` fans out to generated code (`zz_generated.register.go`,
  `zz_generated.deepcopy.go`, clientset/lister/informer, CRDs). Two PRs that both touch
  `api/**` conflict on the *generated* files even if their own diffs don't overlap.
- The expensive test suite (conformance, policy-library, k6, perf) only runs on `push` to
  `main`/`release-*`, never on `pull_request` — a bad interaction between two merged PRs
  lands on `main` undetected. `e2e-gate.yaml` turns that into a reactive block: once that
  suite fails, a required "E2E Gate" status goes red on every open PR targeting the broken
  branch until the tracking issue closes or the PR carries `e2e-gate-bypass`.
- A red "E2E Gate" check does **not** block `ready-for-review` — that label only gates on
  DCO/CI/conflicts/unresolved-threads. `gate_blocked` and `ready-for-review` are independent
  signals; a PR can carry both, and `pr-queue` states them separately rather than conflating
  them.

See `profiles/maintainer/skills/kyverno-context/SKILL.md` for the full codegen/CI reference and
`profiles/maintainer/skills/pr-queue/SKILL.md` for how it's used in sequencing.

## Why labels and CODEOWNERS are never hardcoded

`KYVERNO_REPO` can point at `kyverno/kyverno` or a sandbox repo, each with its own label set
and CODEOWNERS file. `kyverno-context` resolves both live every session (`list_label`,
`get_file_contents` on `CODEOWNERS`) rather than assuming it already knows a repo's
conventions — the same principle extends to `AGENTS.md`/`ARCHITECTURE.md` (cached into
Mnemosyne by `scripts/install.sh`, recalled instead of re-fetched) and to arbitrary repo
questions (`search_code`, scoped with `repo:${KYVERNO_REPO}` explicitly — it is not
auto-scoped).

`search_code` approximates "what would this break" via text search — no real call graph, so
it misses interface-based indirection in Go. A `gopls`-backed MCP server for real
call-graph edges is a tracked future item, not built — see `docs/workflow.md`'s build plan.

## Maintainer questions are open-ended — skills aren't scripts

A maintainer can ask anything about the queue's or an issue's state, not just a fixed set of
actions. `pr-queue`/`pr-actions`/`issue-triage`/`issue-actions` are written as tools + fixed
facts + one generalizing instinct — when something about the repo's state is unknown, look
it up and cite it, never guess — rather than a fixed procedure per anticipated question.

## Two independent Slack integrations, same bot token

- **Hermes' `platforms.slack` adapter** — how the maintainer talks to the agent (mentions,
  DMs). Uses `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` over Socket Mode via Hermes' built-in Bolt
  integration.
- **The `slack` MCP server** (`korotovsky/slack-mcp-server`) — a *tool* the agent calls to
  read `SLACK_HOME_CHANNEL`'s history and post into it. Talks to Slack's Web API directly;
  no concept of Socket Mode or an app-level token. Given `SLACK_BOT_TOKEN` (as
  `SLACK_MCP_XOXB_TOKEN`), never `SLACK_APP_TOKEN`.

If Slack replies work but the queue never posts (or vice versa), these are two independent
code paths — check which one is actually failing before assuming a single token problem.

Hermes runs exactly one gateway *per host*, not per profile, as the inbound process for every
profile's messaging platforms — `kyverno chat` only starts a foreground CLI session; it never
makes anything listen for a Slack mention. The gateway is installed once from the `default`
profile (`hermes gateway install`), serving every profile including `kyverno`; a per-profile
`hermes gateway run`/`install` fails on purpose once a host gateway exists, to guard against
double-binding one bot token from two pollers. `scripts/install.sh` installs it automatically
when Slack credentials are present.

The sandboxed terminal (OpenShell policy, audit trail) is described in `docs/openshell.md`; the
longer design (role-based bots, contributor profile) is in `docs/openshell-architecture.md`.

## What's not built yet

`docs/workflow.md` is the target spec and its own "Build plan" section is the current,
phased plan for the gap between that spec and what's described above — issue work, the
dashboard, session open/close, and the remaining PR decide-table rows. Read it there, not
duplicated here.
