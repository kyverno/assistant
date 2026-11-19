# Issue management: where it stands and what comes next

Status and plans for the issue management. Update it as these land.

## Not in the setup launcher yet

`assistant setup` offers the PR automation and runs the adoption for it. Issue management is
**not** offered there. It is only adopted by running `scripts/adopt-ci.sh --issues` by hand.

Why: the PR automation went in after it had been tested on real repos. The issue sweep has only had offline tests and
read-only dry-runs. It goes into the launcher once it has been trialled on a few repositories (the spare repo first,
then real ones, Kyverno included).

## Planned

1. **Launcher support for the sweep.** Offer issue management in `assistant setup` next to the PR automation, driven by
   the user's choices: whether to adopt it, their own label names (what `--issue-config` takes today), whether the
   possibly-resolved rule is on, and whether review queues are used.
2. **More in issue management if needed.** Rules that sort issues without a model (labels, keywords, linked PRs), more
   of the queue report, and the prow commands (`/label`, `/remove-label`) for people.

## Open points

- A merge commit (or a pin bump after a squash) is needed so `issue-management.yml`'s pin stays in main's history.
