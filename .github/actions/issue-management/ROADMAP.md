# Issue management: where it stands and what comes next

Status and plans for the issue management and the queue-triage workflow. Update it as these land.

## Not in the setup launcher yet

`assistant setup` (added to main in #65) offers the PR automation and runs the adoption for it. Issue management is
**not** offered there. It is only adopted by running `scripts/adopt-ci.sh --issues` by hand.

Why: the PR automation went in after it had been tested on real repos. The issue sweep and the queue-triage agent have
only had offline tests and read-only dry-runs, and the agent has never run at all. They go into the launcher once they
have been trialled on a few repositories (the spare repo first, then real ones, Kyverno included).

## Planned

1. **Launcher support for the sweep.** Offer issue management in `assistant setup` next to the PR automation, driven by
   the user's choices: whether to adopt it, their own label names (what `--issue-config` takes today), whether the
   possibly-resolved rule is on, and whether review queues are used.
2. **Automate the `gh aw add` step.** Today it is a manual follow-up (`gh aw add`, edit the queues, `gh aw compile`,
   commit). The launcher should do it for the user's chosen repositories and queue labels, and open the PR the same
   way the other adoptions do. The workflow ships staged (previews only); going live stays a deliberate edit.
3. **More in issue management if gh-aw does not work out.** If the agent's classification is poor or the billing path
   does not suit organisations, grow the deterministic side instead: rules that sort issues without a model (labels,
   keywords, linked PRs), more of the queue report, and the prow commands (`/label`, `/remove-label`) for people.

## Open points

- The queue-triage workflow has no per-issue limit across label calls; "one queue per issue" rests on the prompt.
- Its staged output has not been seen on GitHub yet.
- A merge commit (or a pin bump after a squash) is needed so `issue-management.yml`'s pin stays in main's history.
- This branch predates #65 and later PR-automation commits; merge or rebase onto main before it lands, and expect
  `scripts/adopt-ci.sh` and the adoption docs to need reconciling with the launcher change.
