# kyverno-fetch

A bundled Hermes plugin, not a skill. Registers five tools:

- `fetch_pr_candidates` — what `pr-queue` calls instead of `search_pull_requests` plus one
  `pull_request_read` per candidate — the actual fix for a real session that took 15 of 107
  PRs, fetched one at a time, ~5 minutes. The model sees one tool call; this plugin's own
  Python code makes the real network requests (a search pass, then one GraphQL detail call
  per candidate, run concurrently via a thread pool) directly against
  `https://api.github.com/graphql`.
- `fetch_pr_details` — full detail for an explicit list of PR numbers, no search step. The
  other half of a digest-then-rank pass: call `fetch_pr_candidates(detail="digest")` to rank
  a whole candidate set cheaply, then this for just the numbers chosen.
- `fetch_issue_candidates` — the same shape for `issue-triage`/`issue-actions`: a search
  pass plus one concurrent GraphQL detail call per issue. Per issue, resolves every PR that
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
`github` MCP server, no new credential, no MCP server involved for any call.

Ships automatically on `hermes profile install .`, same as `kyverno-sequencer` (see
`docs/architecture.md`). Pure stdlib (`urllib.request`, `concurrent.futures`) — no
external dependency to install.
