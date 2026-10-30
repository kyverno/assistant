# Adopting the CI actions

Part of the [kyverno-assistant README](../README.md#ci-automation-for-your-repositories). These actions are independent
of the agent.

You need no install, image or model. One person runs one command; it opens a pull request in each repository you
name. Reviewing and merging that PR is the adoption.

## For a Kyverno repository or any other organisation

```bash
git clone https://github.com/kyverno/assistant && cd assistant
gh auth login                                   # your own login; nothing is stored
scripts/adopt-ci.sh --reviewers @your-org/maintainers your-org/repo-a your-org/repo-b
```

For each repository the script clones it, adds the files below on a branch, signs the commit off (`-s`), pushes
(to your fork if you cannot push to the repository) and opens the PR. Use `--dry-run` first to see the file list
without pushing anything. It never overwrites a file that already exists, and skips a repository that already has
the PR open, so it is safe to run again.

| Added to the repository | Purpose |
| --- | --- |
| `.github/workflows/pr-management.yaml` | readiness (hourly and on PR events) and stale (daily), **preview only** |
| `.github/workflows/pr-labelling.yaml` | path labels and merge-conflict label, preview only |
| `.github/workflows/sync-labels.yaml` | creates the lifecycle labels, run by hand |
| `.github/workflows/check-pr-management.yaml` | validates the config on every PR, no token needed |
| `.github/pr-management.json` | reviewer team, exempt labels, timings |
| `.github/pr-readiness-checks.yml` | which checks block a PR, which are not the author's to fix |
| `.github/labels.yml` | label catalog and path rules |

The workflows call `kyverno/assistant/.github/actions/pr-management@<full SHA>`. The SHA is this checkout's commit, so the
action cannot change under the adopter; to update, change the SHA (a Dependabot `github-actions` entry opens that
PR). The workflow files are the only code the repository carries; the logic stays here.

### Updating a repository that already uses it

`scripts/adopt-ci.sh` never overwrites a file, so running it again on an adopted repository adds nothing. To move
the pin, including from another source such as `kyverno/kyverno/.github/actions/pr-management`, run

```sh
scripts/adopt-ci.sh --update-pin your-org/repo-a your-org/repo-b
```

It rewrites only the pinned `uses:` reference in the repository's workflows to this checkout's commit and opens a PR
saying which lines moved from where to where. Config files, labels and every other workflow line stay as they are, and
`--reviewers` is not needed. A repository that does not use the action yet is skipped with a pointer to the normal
adoption. `--dry-run` shows the diff and pushes nothing.

### Turning it on

Nothing happens on its own after the PR merges, by design: every scheduled and PR-event run only previews.
A repository admin turns it on, after reading the previews, in this order.

**Before the switch**

1. Run **Sync labels** from the Actions tab with dry run off. Readiness and stale use `needs-author-action`,
   `ready-for-review`, `workflow-approval-required` and `stale`, and runs can fail if those do not exist.
2. Read a few preview runs (the log lines start with `[dry-run]`), and optionally run **PR lifecycle** by hand,
   readiness and then stale, with dry run off to see real effects once.
3. Edit `.github/pr-management.json` (reviewer team, exempt labels, timings) and `.github/pr-readiness-checks.yml`
   (which failing checks block a PR, and which are not the author's to fix) to match the repository.
4. An organisation that restricts which actions may run (Settings, Actions, General) must allow
   `kyverno/assistant@*`, `actions/labeler@*` and `eps1lon/actions-label-merge-conflict@*`, or the runs will not start.

**The switch**

Set the repository variable `PR_AUTOMATION_LIVE` to `true` (Settings, Secrets and variables, Actions, Variables), or:

```bash
gh variable set PR_AUTOMATION_LIVE --body true --repo owner/repo
```

Creating a variable needs admin access to the repository. No PR and no workflow edit is needed.

**After it**

- Readiness and path labels act on the next PR event, readiness also hourly, and stale in its daily run.
- The first live stale run can mark old quiet PRs stale. They cannot be closed until at least 7 days after the
  warning, so there is a week to react.
- To pause, set the variable to `false` or delete it; every scheduled and PR-event run goes back to preview.
  Manual runs ignore the variable and follow their own dry-run checkbox.

### Trust and permissions

The action runs with the workflow's own `GITHUB_TOKEN`: no secret and no cross-repository credential is created.
Write-capable jobs check out the default branch, never a PR head, and read their config from there. The token
permissions each mode needs are in [`.github/actions/pr-management/README.md`](../.github/actions/pr-management/README.md).

## Why a script, and what else was considered

| Approach | Used by | Why not the main route |
| --- | --- | --- |
| **Pull request from a script run with your own login** (chosen) | bulk-change tools such as [multi-gitter](https://github.com/lindell/multi-gitter) and Turbolift, which run a change across many repositories and open a PR in each | Needs no server and no stored credential, and the PR is the review step |
| **Starter workflows** in the organisation's `.github` repository (`workflow-templates/`) | GitHub; they appear under Actions, New workflow, in every repository of the organisation ([docs](https://docs.github.com/en/enterprise-server@3.4/actions/using-workflows/creating-starter-workflows-for-your-organization)) | Organisation-only, and each repository has to click through it by hand; no PR, no config. Worth adding for Kyverno as a second route |
| **Onboarding PR from a GitHub App** | Renovate opens a "Configure Renovate" PR, and merging it turns the tool on | The right long-term shape, but it needs a hosted app and write access to every repository, which is the cross-repository credential this design avoids |
| **Required workflows through organisation rulesets** | GitHub, to force a workflow onto every repository | Enforces rather than adopts; the 2023 announcement limited it to Enterprise plans, so other organisations may not have it, and it does not carry per-repository config |
| **Reusable workflow** instead of a composite action | GitHub | Can hold several jobs, but a checkout inside it checks out the caller's repository, and it cannot be published to the Marketplace. Composite fits a step that runs in the caller's job |

## Not decided yet

- Whether to also offer the whole workflow as a reusable workflow (`workflow_call`), so a repository keeps only a
  thin caller. It needs checking how `schedule`, `pull_request_target`, concurrency and the `PR_AUTOMATION_LIVE`
  variable behave in a called workflow, and that a local action path inside it resolves in the caller's checkout, not
  here.
- Making the config files optional, so a repository would need only the workflows, needs a change to the action's
  `run.py`. That would cut the number of files adopters review.
