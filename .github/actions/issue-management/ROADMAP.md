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

## Trial result (queue triage)

Run once, staged, in kyverno/test-ai-assistants on 10 seeded copies of issues Jim had already labelled (first 1,200
characters, no comments). The org's Copilot billing worked through `copilot-requests: write`; nothing was written.
It agreed with Jim on 6 of 10. The misses were verify-closure judged as needs-repro (it could not see that a fix had
shipped) and scope-review judged as decision (it read a feature request as a decision). The seeds lacked the
discussion Jim had, so the real rate is unknown.

The triage also posts one short comment per labelled issue with the reason, so the reason is on the issue and not only
in the run log (`add-comment`, same cap as the labels, staged with them).

The prompt now tells the agent to check the evidence first (the whole discussion, merged pull requests and releases,
ownership documents). Not done yet, to be tried only if that is not enough: let the agent read the three most recent
issues of each queue as examples, let it abstain and leave an issue for a person, hand it per-issue facts computed by
the sweep's code, put an area-to-queue table in the prompt. Measure each on a larger seed set with full text and
comments, staged.

## Open points

- The queue-triage workflow has no per-issue limit across label calls; "one queue per issue" rests on the prompt.
- A run lists a default label, `agentic-workflows`; check whether a live run would add it before going live.
- A merge commit (or a pin bump after a squash) is needed so `issue-management.yml`'s pin stays in main's history.
