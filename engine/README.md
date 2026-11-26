# The repository engine

`repo_engine/` is the engine; `serve.py` serves its nine tools over MCP on stdio (`repo_engine/server.py`, standard
library only). Try it with `python3 engine/serve.py` and an MCP client; the maintainer profile starts it as
`mcp_servers.repo-intel`. Set `ASSISTANT_REPO` (owner/name) and `REPO_GITHUB_TOKEN`, and optionally
`ASSISTANT_ORG_FILE`. Tools: `queue` and `item` (`queue_tools.py`, over `core/`), five fetch tools (`fetch.py`, `fetch_tools.py`), the sequencer (`sequencer.py`, `sequence_tools.py`) and `repo_pack` (`pack_tools.py`), which returns the organisation pack's rules and facts.

## Queue and item

`queue(focus, kind, limit, prefer, avoid)` is where `pr-queue` starts: the top items for a focus, ranked in code, one
line each with the reasons, the next step and who takes it, what the code changes, risk, and relations to other PRs.
`item(number, level)` is one PR or issue: a card, or with `level="code"` the patches of the files that matter most and
the nearest `AGENTS.md` excerpt. Each view has a character budget (`core/layers.py`) that tests check. `core/` has
no GitHub calls, and nothing in the engine imports Hermes; `docs/architecture.md` describes the ranking rules.

## Fetch

- `fetch_pr_candidates` — what `pr-queue` calls instead of `search_pull_requests` plus one
  `pull_request_read` per candidate — the actual fix for a real session that took 15 of 107
  PRs, fetched one at a time, ~5 minutes. The model sees one tool call; the engine's own
  Python code makes the real network requests (a search pass, then GraphQL detail queries
  of up to 10 candidates each, 4 at a time) directly against
  `https://api.github.com/graphql`.
- `fetch_pr_details` — full detail for an explicit list of PR numbers, no search step. The
  other half of a digest-then-rank pass: call `fetch_pr_candidates(detail="digest")` to rank
  a whole candidate set cheaply, then this for just the numbers chosen.
- `fetch_issue_candidates` — the same shape for `issue-triage`/`issue-actions`: a search
  pass plus GraphQL detail queries of up to 10 issues each. Per issue, resolves every PR that
  references closing it via `closedByPullRequestsReferences` (GitHub's own closing-keyword
  parser — a real fix for duplicate/competing PRs against the same issue, not a body-text
  regex) and the timeline's actual closer (PR or commit) when it's closed.
- `fetch_issue_details` — the issue-side equivalent of `fetch_pr_details`.
- `fetch_file_diff_overlap` — an on-demand follow-up: two PRs touching the same file doesn't
  mean they touch the same lines. Fetches each PR's real diff hunks for one file via REST
  (GraphQL has no patch field) and checks whether the touched line ranges actually overlap.

`fetch_pr_candidates`/`fetch_issue_candidates` take a `detail` param, `"full"` (default) or
`"digest"`. Digest drops the long free-text fields (body, review/comment text) and caps
`limit` at 60 — small enough to rank or count across an entire candidate set without risking
Hermes' spillover threshold, which this project's own tools otherwise hit in real sessions
(a 15-PR `ready-for-review` fetch alone runs 127K chars, already over the 100K default).
Only worth using when the ranking criterion isn't something a search qualifier
(`milestone:`, `sort:`, a label) already narrows directly — closing-issue severity,
Dependabot semver/security level, "superseded by a merged PR," and similar relational
filters are the real cases.

All five use the profile's own `REPO_GITHUB_TOKEN` — the same scopes already granted to the
`github` MCP server, no new credential; the calls go straight to GitHub's API, not through the GitHub MCP server.

Details come up to 10 PRs or issues to a query, 4 queries at a time; other requests run two at a time. Requests are retried once on a dropped connection or a 502/503/504, and a rate limit says when
to try again. A result over 40,000 characters drops long text first, then trailing items, and names them in
`not_shown`, so nothing spills to a file. Each closing issue lists `other_prs`, every other PR that claims it, and
the gate's open failure issues come with every PR fetch.

Standard library (`urllib.request`, `concurrent.futures`) plus PyYAML to read the organisation pack.

## Sequence

`sequence_prs` is what `pr-queue`
calls with PR numbers. It reads their records from the last fetch (fetching any it has not seen) and gets back a hard-
dependency graph layered into tiers — a graph builder, not a decision maker. Four hard-edge
types, all derived mechanically from the input (stacked branches, generated-file input-before-
output, an explicit "Depends on #N"-style body reference, a closing-issue conflict), cycle
detection, and a topological layering into tiers: within a tier, no hard dependency exists
between any two PRs, and ordering them is the agent's call — Slack context, mnemosyne
history, whatever the maintainer said they care about this session — not this tool's.

The graph itself (`sequencer.py`) is a pure function over its input. The tool around it reads the gate's open failure
issues and the default branch from the repository, so the model never passes them in.

What counts as a generated file, API surface, critical path or generated-file input, and what the branch gate is,
comes from the organisation pack (`maintainer:` in `org/org.yaml`, read by `pack.py`). Unknown keys are an error. With no
pack the tool still runs: every file reads `STANDARD` and no PR is gate-blocked, and the result says `pack: none`.
