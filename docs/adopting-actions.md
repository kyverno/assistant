# Adopting the CI actions

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

The workflows call `kyverno/assistant/actions/pr-management@<full SHA>`. The SHA is this checkout's commit, so the
action cannot change under the adopter; to update, change the SHA (a Dependabot `github-actions` entry opens that
PR). The workflow files are the only code the repository carries; the logic stays here.

### After the PR merges

Nothing happens on its own, by design:

1. Run **Sync labels** from the Actions tab with dry run off.
2. Run **PR lifecycle** by hand, readiness and then stale, with dry run off; read what it did.
3. When you trust the previews, a repository admin sets the repository variable `PR_AUTOMATION_LIVE` to `true`
   (Settings, Secrets and variables, Actions, Variables). No PR is needed. Setting it back to `false`, or deleting
   it, returns every scheduled and PR-event run to preview. Manual runs always follow their own dry-run checkbox.

Edit `pr-readiness-checks.yml` and `labels.yml` to match the repository; the generated ones are generic.

### Trust and permissions

The action runs with the workflow's own `GITHUB_TOKEN`: no secret and no cross-repository credential is created.
Write-capable jobs check out the default branch, never a PR head, and read their config from there. The token
permissions each mode needs are in [`actions/pr-management/README.md`](../actions/pr-management/README.md).

## Why a script, and what else was considered

| Approach | Used by | Why not the main route |
| --- | --- | --- |
| **Pull request from a script run with your own login** (chosen) | bulk-change tools such as [multi-gitter](https://github.com/lindell/multi-gitter) and Turbolift, which run a change across many repositories and open a PR in each | Needs no server and no stored credential, and the PR is the review step |
| **Starter workflows** in the organisation's `.github` repository (`workflow-templates/`) | GitHub; they appear under Actions, New workflow, in every repository of the organisation ([docs](https://docs.github.com/en/enterprise-server@3.4/actions/using-workflows/creating-starter-workflows-for-your-organization)) | Organisation-only, and each repository has to click through it by hand; no PR, no config. Worth adding for Kyverno as a second route |
| **Onboarding PR from a GitHub App** | Renovate opens a "Configure Renovate" PR, and merging it turns the tool on | The right long-term shape, but it needs a hosted app and write access to every repository, which is the cross-repository credential this design avoids |
| **Required workflows through organisation rulesets** | GitHub, to force a workflow onto every repository | Enforces rather than adopts; the 2023 announcement limited it to Enterprise plans, so other organisations may not have it, and it does not carry per-repository config |
| **Reusable workflow** instead of a composite action | GitHub | Can hold several jobs, but a checkout inside it checks out the caller's repository, and it cannot be published to the Marketplace. Composite fits a step that runs in the caller's job |

## Not decided yet

- The action's path is `kyverno/assistant/actions/pr-management`, because that is where it lives today. The two
  Kyverno PRs for `website` and `policies` pin `kyverno/kyverno/.github/actions/pr-management` instead; decide
  with the owner of kyverno/kyverno#18275 which path everyone should pin before the script is used widely.
- Making the config files optional, so a repository would need only the workflows, needs a change to the action's
  `run.py`. That would cut the number of files adopters review.
