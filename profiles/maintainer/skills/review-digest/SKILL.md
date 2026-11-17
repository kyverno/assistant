---
name: review-digest
description: "The weekday review digest posted to Slack: what changed since the last digest, and the PR queue with what each PR needs."
version: 0.2.0
author: Suhaani Agarwal
license: Apache-2.0
platforms: [linux, macos, windows]
---

# review-digest Skill

For the scheduled `review-digest` job, which posts to Slack with nobody to ask. A maintainer reads it over coffee: what
moved since the last digest, then what to do with the queue. Not `pr-queue`: no diagnosis of every bot PR, no
dashboard, no questions.

## Procedure

1. `repo_pack` for the ready labels (`readiness.ready_labels`) and the severity labels of closing issues. No ready
   labels: post one line saying the repository has not adopted them, and stop.
2. `fetch_pr_candidates(repo=ASSISTANT_REPO, search_query="label:<ready label>", detail="digest", limit=60)`.
3. Since last time: your previous digest is in context. Take the PR numbers it listed that are not in the fetched set
   and call `fetch_pr_details` once for them (at most 10). `state` and `merged_at` say merged or closed. Count the new
   PRs (in the set, not in the previous digest). No previous digest: leave the "Since" section out.
4. No other tool calls: no `sequence_prs`, no diffs, no Slack or Discussions, no dashboard, no memory writes.

## Ranking and the action for each PR

Rank by the closing issue's severity label, then by how close the PR is to merging, then by age (oldest first, so
nothing rots). Work out what each PR needs, using the first line that fits:

- Red CI: "CI red, author to fix" (name the failing gate if known). `changes_requested`: "author to address requested
  changes".
- `merge_state` DIRTY: "needs a rebase". BLOCKED with green CI: "waiting on a required review".
- Unresolved threads: "N open threads to resolve".
- Green, `bot_approved`, no open threads: "ready to approve" with `human_approvals` so far ("0 of the 2 approvals",
  or "1 approval, needs one more"; if the repository's rule is not known, just say how many approvals it has).
- Anything else: "needs a first review".

Put a PR under **Review now** when its action is for a maintainer (approve, review, resolve), and under **Waiting on
the author** when the next move is the author's. Show the age ("opened 12d ago") or "no activity for 9d" only when it is
over a week.

## Output

Only the digest, in this shape, nothing before or after. Plain Markdown (Hermes converts it for Slack). Aim for 1,500 to
2,500 characters; never more than 3,000.

```
**Review digest · owner/repo · Mon 17 Nov**
14 ready for review · 3 new · 2 left the queue

**Since the last digest**
- Merged: [#18252](url) chi v5.4.0, CVE fix · [#18234](url) namespaceSelector fix
- Closed without merging: [#18100](url) title
- New: [#18301](url) title, [#18305](url) title (green, bot-approved)

**Review now** (maintainer action)
1. [#18252](url) chi v5.4.0, CVE fix: ready to approve, 0 approvals so far, closes a critical issue
2. [#18290](url) CEL cache for policies: 2 open threads to resolve, opened 9d ago
(at most 6, one line each: what it does, then what is needed)

**Waiting on the author**
- [#18613](url) E2E gate hardening: CI red (conformance gate), no activity for 8d
- [#18590](url) title: needs a rebase
(at most 5, the same shape)

**Bot PRs:** 5 Dependabot bumps, 3 green, one major (#18400 go-jose v4)

**Heads-up**
- At most two lines, only things that change what to do first: a failing branch gate, two PRs editing the same file,
  a PR that has waited the longest.
```

- Dependabot PRs are one line in total, never a line each, unless one is a major or a security bump.
- Leave out any section that would be empty. Titles are shortened to what the PR does, not copied.
- Never write what you did not check, how you built the list, or that anything was skipped. If the fetch fails, one
  line: "Could not build the digest: <reason>".
- No ready PRs and nothing changed: "Nothing is waiting for review."
