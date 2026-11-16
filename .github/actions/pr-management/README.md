# Shared PR management

Reusable composite action for any GitHub organisation. Runs on `ubuntu-latest` with
Bash, Python 3.9+, Ruby (standard-library YAML/JSON), `jq`, and `gh`; no npm/pip
packages or repository-local tool actions are needed. Scripts are loaded from
`GITHUB_ACTION_PATH`, not the consuming checkout.

## Interface

| Input | Default | Meaning |
| --- | --- | --- |
| `mode` | required | `labels`, `readiness`, `stale`, `sync-labels`, or `validate` |
| `config` | `.github/pr-management.json` | Repository-specific JSON |
| `readiness-checks` | `.github/pr-readiness-checks.yml` | Optional: your changes to the built-in readiness checks |
| `dry-run` | `'true'` | Exactly `'true'` or `'false'`; preview never mutates GitHub |
| `stale-days` | `'7'` | Author inactivity before marking stale |
| `review-nudge-days` | `'7'` | Maintainer inactivity before a reviewer reminder |

Supply `GH_TOKEN` in the caller step's environment, except for `validate`, which
requires no token or API access. The event and PR number come from GitHub's event
file. `readiness` handles `pull_request_target` opened/synchronize/reopened/
ready_for_review/converted_to_draft, hourly schedule, and workflow_dispatch.
`stale` normally runs daily/on demand; a draft transition in either mode clears
`ready-for-review`, `needs-author-action`, and `workflow-approval-required`.
Drafts and Dependabot are otherwise excluded from readiness/stale processing.

`labels` renders the metadata+rules label catalog for pinned `actions/labeler`.
On PR events, production mode applies path labels; on default-branch pushes it
refreshes merge-conflict labels. Preview prints the rendered rules, **not** an
exact matching simulation, and skips both external mutating actions.
`sync-labels` creates missing labels and updates changed colors/descriptions.
It never deletes unmanaged labels and fails on lookup or write errors.

`validate` checks JSON, label metadata/rules, and compiles all readiness jq
expressions without evaluating them, regardless of `dry-run`. It never calls
GitHub. Consumers can use it in read-only `pull_request` CI on local/head
configuration while pinning the action itself to a trusted full commit SHA.
In `validate` mode, with the action pinned to a trusted SHA, the PR's
configuration is only parsed as YAML/JSON and its readiness jq is compiled but
never invoked; no PR-provided shell, Python, or jq program is executed. Other
modes (notably `readiness`, which evaluates the jq checks) still require the
trusted default-branch checkout described below.

## Trusted configuration and permissions

For mutating or scheduled modes, **always check out the repository's default
branch**, never a PR head/merge ref, and set `persist-credentials: false`.
Readiness jq is executable data transformation, so only trusted default-branch
checks may be evaluated with a write-capable token. All configuration paths must
be files inside the checkout (including their resolved symlink destinations).
Do not run PR-provided code in these write-capable jobs.

```yaml
permissions:
  contents: read
  issues: write
  pull-requests: write
  checks: read
  actions: read

jobs:
  readiness:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.repository.default_branch }}
          persist-credentials: false
      # Replace <full-commit-SHA> with a reviewed commit before use.
      - uses: kyverno/assistant/.github/actions/pr-management@<full-commit-SHA>
        env:
          GH_TOKEN: ${{ github.token }}
        with:
          mode: readiness
          dry-run: 'true'
```

Least-privilege scopes per mode:

| Mode | Required token permissions |
| --- | --- |
| `labels` | `contents: read`, `pull-requests: write` |
| `sync-labels` | `contents: read`, `issues: write` |
| `readiness` | `contents: read`, `issues: write`, `pull-requests: write`, `checks: read`, `actions: read` |
| `stale` | `contents: read`, `issues: write`, `pull-requests: write` |
| `validate` | `contents: read` (no token required) |

`labels` mode only applies path and `merge-conflicts` labels; it does not create
label metadata. Run `sync-labels` (for example on pushes to the default branch
that change `labels.yml`) so lifecycle labels such as `needs-author-action`,
`ready-for-review`, `workflow-approval-required`, and `stale` exist before
enabling readiness or stale mutations.
For `validate`, use only `contents: read`; checkout the proposed config and omit
`GH_TOKEN`. Use separate jobs for each mode, explicit timeouts, and non-cancelling
concurrency groups to avoid overlapping sweeps. Do not expose write-capable jobs
to `pull_request` or other untrusted-code execution.

## Repository configuration

All keys below are mandatory except the optional `infra_checks` and `nudge_plain_comments`; unknown keys fail validation.

```json
{
  "fallback_reviewers": "@your-org/maintainers",
  "exempt_labels": ["no-stale", "hold"],
  "close_days": 14,
  "warning_days": 7,
  "backlog_days": 30,
  "milestone_prefix": "",
  "labels_file": ".github/labels.yml"
}
```

Day values must be JSON integers from 1 through 3650; input day overrides must be
positive whole numbers in the same range. `stale-days` and `warning_days` must not
exceed `close_days`. Exemption labels are exact, case-sensitive names and apply
to **all** stale/idle-review handlers, and readiness leaves a PR with one untouched. Adding an exemption removes an existing
`stale` label, so removing the exemption later starts a fresh cycle and warning
period. `fallback_reviewers` is an
`@organization/team`. An empty `milestone_prefix` disables milestone stamping;
Kyverno, for example, uses `Kyverno Release ` and stamps qualifying same-repository issues
during readiness sweeps.

`nudge_plain_comments` (optional, default `false`) makes readiness ask a reviewer about a plain comment. When a
PR that is otherwise `ready-for-review` has a maintainer's comment (a PR comment, or the summary of a review that only
comments) that the author has not answered, the bot posts, naming the reviewer, linking the comment and quoting its first 100
characters (with `@`, `#`, `<` and `>` removed, so the quote pings, links and injects nothing), and asks whether it is a
requested change. It never changes a label. A requested change belongs in a **Request changes** review, which the
`unanswered` check tracks; a reviewer who does not want a formal review can add an exempt label such as `hold`, which
stops the bot changing labels on that PR. It errs towards staying quiet:

- Only when the PR has no blocking finding, no pending or gated checks and no exempt label, so a PR the author already
  owes action on is never nudged.
- At most once per head commit, so reviewers talking to each other do not trigger a nudge per message.
- A comment is answered by the author's later comment, review or reply in a review thread, or by a push. A review is for
  one commit, so a different head means a push; a PR comment has no commit, so the head commit's date stands in.
- Not for inline comments, nor for the summary of a review that has inline comments: those are review threads, which the
  `threads` check already tracks.
- Not for `/commands`, a bare LGTM or thanks, or a reviewer who approved afterwards.
- Only comments from the last 7 days, so enabling it never digs up old threads. Run the workflow once with `dry_run`
  to see how many nudges the first sweep would post.

Maintainers are GitHub's OWNER, MEMBER and COLLABORATOR associations. Comments do not trigger the workflow, so a nudge
appears on the next hourly sweep.

The label file maps each label name to `color` (quote six-digit hex values),
`description`, and optional actions/labeler `rules`. Rules are rendered without
changing matcher semantics. Metadata-only entries are not path-label rules.

## Readiness checks

Readiness runs a built-in set, so a repository needs no check logic of its own:

| Check | Blocks when |
|---|---|
| `dco` | the DCO check failed |
| `ci` | any other check failed, except those in `infra_checks` |
| `infra_checks` | a check named in `infra_checks` is red; an FYI only, it never blocks |
| `conflicts` | the PR has `merge-conflicts` |
| `threads` | a review thread is unresolved and its last comment is not the author's |
| `unanswered` | a maintainer's latest review is *Request changes* and the author has neither replied nor pushed since |

A check can run more than once on a commit, and GitHub lists every run. Only the newest run of each check counts,
so a red run that was later re-run green no longer blocks the PR; a re-run that has not finished counts as pending.
This applies to the built-in checks and to a repository's own checks alike.

The jq for each is in [`default-readiness-checks.yml`](default-readiness-checks.yml). A repository changes them in
`.github/pr-readiness-checks.yml`, which is optional:

```yaml
disable: [unanswered]          # turn a built-in check off
threads:                       # same name as a built-in: replaces it
  test: '...'                  # jq, evaluated against the PR, with $author bound to its login
  message: '"..."'             # jq string for the comment
needs_description:             # any other name: adds a check
  test: '(.body // "") | length < 20'
  message: '"describe what this PR changes and why"'
```

Red checks that are not the author's to fix go in `pr-management.json`, so `ci` keeps its logic:
`"infra_checks": {"E2E Gate": "base-branch or CI-infrastructure issue"}`. Replace a built-in only when this is not
enough: you then own that check and no longer receive changes to it. Fields available to jq are those requested by
`detail_query` in `readiness.sh`; `unanswered` sees the newest 100 comments and 100 reviews.

## Safety and rollout

Readiness finishes label/check/thread pagination before evaluation. Missing CI
data and pending/gated workflows are not ready; failed lookups and partial data
never prove readiness. A remaining truncated connection may remove a stale
ready label but never clears author-action/approval labels. The head/state/draft
are rechecked before readiness changes. Comments are paginated for marker
deduplication.

Stale handling preserves commit/force-push/merge-conflict clocks, conservative
observation of backdated pushes, and bot-only persisted markers. The "first saw this push" marker is a short
visible comment that the bot minimizes right after posting; minimizing is cosmetic, so a failure only warns. Author replies
do not reset inactivity. Auto-close requires both total `close_days` inactivity
**and** at least `warning_days` since a matching bot warning or bot-applied stale
label. A manually applied stale label without a bot warning is insufficient.
An old backlog PR cannot be marked and closed in the same sweep. State, draft
status, and exemptions are rechecked from fresh data; close additionally checks
live state/head/labels immediately before writing. API races cannot be made
transactional, but changed/unknown data fails conservatively.

Start new repositories with readiness/stale/sync in preview. Inspect scheduled
logs and reviewer mentions, provision label metadata, then enable each mode
separately by explicitly setting `dry-run: 'false'`. Keep manual dispatch preview
as the default. Labels can be enabled independently to preserve existing
path labeling while other modes are previewed.


## Origin

Extracted from kyverno/kyverno#18275 (Apache-2.0). See [`NOTICE`](../../../NOTICE).
