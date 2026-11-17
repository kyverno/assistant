# Adopting the CI actions

Part of the [README](../README.md#prepare-your-repository). These actions are independent
of the agent.

You need no install, image or model. One person runs one command; it opens a pull request in each repository you
name. Reviewing and merging that PR is the adoption.

## For a Kyverno repository or any other organisation

```bash
git clone https://github.com/kyverno/assistant && cd assistant
gh auth login                                   # your own login; nothing is stored
scripts/adopt-ci.sh --reviewers @your-org/maintainers your-org/repo-a your-org/repo-b
```

Without the clone, from the launcher (needs Docker and a released image): `assistant setup your-org/repo-a`. It also offers the
organisation pack. `--reviewers` is optional: the first team of the repository's organisation named in its CODEOWNERS is used.
The shared actions are pinned to the current commit of this repository's default branch, and a commit that is not on it is refused.

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

The four workflow files are short callers: they say when to run and call a shared workflow here
(`.github/workflows/pr-management.yml`, or `pr-management-validate.yml` for the config check), pinned to a commit
of this repository's default branch. The logic, the default-branch checkout and the preview or live decision live in the shared
workflow. To update, change the SHA, or use `--update-pin` below. Job names now read `<job> / <shared job>`, which
matters if a branch-protection rule requires one by name.

### Updating a repository that already uses it

`scripts/adopt-ci.sh` never overwrites a file, so running it again on an adopted repository adds nothing. To move
the pin, including from another source such as `kyverno/kyverno/.github/actions/pr-management`, run

```sh
scripts/adopt-ci.sh --update-pin your-org/repo-a your-org/repo-b
```

It changes only the pinned `uses:` lines that name `pr-management` (the action or a shared workflow) to this
checkout's repository and commit, and opens a PR listing what moved. Config files, labels and every other workflow line stay as they are, and
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

### Issue management

Issue management is a separate adoption, with its own PR, so a repository can take it before or after the PR
automation:

```bash
scripts/adopt-ci.sh --issues your-org/repo-a your-org/repo-b
```

It adds `.github/workflows/issue-management.yaml` (daily, preview) and `.github/issue-management.json`, and never
overwrites a file.
`--reviewers` is not needed: its comments mention no team. It opens the PR from `ci/adopt-issue-management`, warns about
an existing `actions/stale` or similar workflow, and supports `--dry-run`. `--update-pin` moves the pins of both
automations in a repository.

Nothing happens until an admin sets the repository variable `ISSUE_AUTOMATION_LIVE` to `true`, separate from
`PR_AUTOMATION_LIVE`. Until then every scheduled run writes what it would do to the job summary.

Labels work as they do for PRs: the adoption PR adds `needs-info` and `stale-issue` to
`.github/labels.yml` (keeping any it already has) and adds the **Sync labels** workflow. After merging, run **Sync
labels** by hand with dry run off to create them. The action itself never creates a label; a preview, and a live run,
list any configured label the repository lacks, and a rule that needs a missing label does nothing until it exists.
`needs-info` is applied by people, so the waiting-on-the-reporter rule only acts once someone uses it. To reuse labels
you already have, write a config that names them and pass it with `--issue-config my-config.json`: it becomes
`.github/issue-management.json`, and only the labels it leaves at their defaults are added to `labels.yml`. `stale-issue` is used rather than `stale`, which the PR automation owns. The possibly-resolved rule is off by
default; set `"resolved_label"` in the config to turn it on (`possibly-resolved` is added to `labels.yml` only if you
name it, and any other name is treated as a label you already have). The rules and keys are in
[`.github/actions/issue-management/README.md`](../.github/actions/issue-management/README.md).

### Issue queue triage (agentic)

`workflows/issue-queue-triage.md` is a [GitHub Agentic Workflow](https://github.github.com/gh-aw/): once a day it sorts
open issues that are in no review queue into one queue each, by adding a label and one short comment that says why. It
never closes, edits or removes anything, and a person still reviews every queue. A labelled issue is in a queue, so the
next run does not look at it again: the labels are the record of what was sorted, and each run takes the oldest issues
still in no queue. Before choosing it reads the whole issue with its comments,
looks for merged pull requests that mention it and for the latest releases, and reads the repository's ownership
documents (CODEOWNERS, ARCHITECTURE.md and similar) once per run, so a fix that shipped or a different owner can
show. Its reason for each issue names that evidence. It is not a shared `uses:` workflow like the others: gh-aw
copies it into your repository and compiles a lock file there, so each repository commits both files.

```bash
gh extension install github/gh-aw
cd your-repo
gh aw add kyverno/assistant/issue-queue-triage      # adds .github/workflows/issue-queue-triage.md and its .lock.yml
```

The queue names and their definitions in it are Kyverno's. Edit the `allowed` list, the `skip-if-no-match` query and the
definitions in the prompt to match your queues, and create the labels first: a label that does not exist is rejected.
Then run `gh aw compile` and commit both files. `gh aw update` later pulls upstream changes and keeps your edits.

It ships in **preview**: `staged: true` in its `safe-outputs` makes a run report the labels it would add and write
nothing to any issue. Read a few runs, then delete that line, run `gh aw compile` and commit to let it label. Unlike the
sweep this switch is a committed edit, not the `ISSUE_AUTOMATION_LIVE` variable, because gh-aw fixes it at compile time.

It runs on a daily schedule (scattered to a fixed time) and only when the `skip-if-no-match` search finds an issue in
no queue. gh-aw also adds a manual trigger. The agent job holds `contents: read`, `issues: read` and
`copilot-requests: write`, which lets it use the built-in token for Copilot, billed to the organization, so no secret
is stored. The labels are applied by a separate job. If the organization's billing does not allow that, remove
`copilot-requests: write` and set a `COPILOT_GITHUB_TOKEN` secret instead. Read the compiled lock file before relying on
it, as with any generated workflow.

## Why a script, and what else was considered

| Approach | Used by | Why not the main route |
| --- | --- | --- |
| **Pull request from a script run with your own login** (chosen) | bulk-change tools such as [multi-gitter](https://github.com/lindell/multi-gitter) and Turbolift, which run a change across many repositories and open a PR in each | Needs no server and no stored credential, and the PR is the review step |
| **Starter workflows** in the organisation's `.github` repository (`workflow-templates/`) | GitHub; they appear under Actions, New workflow, in every repository of the organisation ([docs](https://docs.github.com/en/enterprise-server@3.4/actions/using-workflows/creating-starter-workflows-for-your-organization)) | Organisation-only, and each repository has to click through it by hand; no PR, no config. Worth adding for Kyverno as a second route |
| **Onboarding PR from a GitHub App** | Renovate opens a "Configure Renovate" PR, and merging it turns the tool on | The right long-term shape, but it needs a hosted app and write access to every repository, which is the cross-repository credential this design avoids |
| **Required workflows through organisation rulesets** | GitHub, to force a workflow onto every repository | Enforces rather than adopts; the 2023 announcement limited it to Enterprise plans, so other organisations may not have it, and it does not carry per-repository config |
| **Reusable workflow** | GitHub | Used here on top of the composite action: the action is the engine, and the reusable workflows keep each repository's callers short |

## Not decided yet

- Making the config files optional, so a repository would need only the workflows, needs a change to the action's
  `run.py`. That would cut the number of files adopters review.
