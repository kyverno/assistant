---
name: review-digest
description: "The short weekday review digest posted to Slack: a few PRs to review first, what is blocked, what changed since last time."
version: 0.1.0
author: Suhaani Agarwal
license: Apache-2.0
platforms: [linux, macos, windows]
---

# review-digest Skill

For the scheduled `review-digest` job, which posts to Slack with nobody to ask. Short and scannable: a maintainer
reads it in ten seconds. It is not `pr-queue`: no full queue, no diagnosis of every bot PR, no dashboard.

## Procedure

1. `repo_pack` for the ready labels (`readiness.ready_labels`), and for the severity labels of closing issues.
   No ready labels: post one line saying the repository has not adopted them, and stop.
2. One call: `fetch_pr_candidates(repo=ASSISTANT_REPO, search_query="label:<ready label>", detail="digest", limit=60)`.
   Make no other tool call: no `sequence_prs`, no diffs, no Slack or Discussions, no dashboard, no memory writes.
3. Rank, in this order: the closing issue's severity label, then CI green and `bot_approved` with zero unresolved
   threads, then most recently updated. Red CI, merge conflicts and `BLOCKED` go to Blocked, not to Review first.
4. Since last time: your previous output is in context. Compare PR numbers: count the new and the gone (merged or
   closed). No previous output: leave the line out.

## Output

Only the digest, in this shape, and nothing before or after it. Plain Markdown (Hermes converts it for Slack). At most
1,200 characters.

```
**Review digest · owner/repo · Mon 17 Nov**
12 ready · 3 new · 2 gone since the last digest

**Review first**
1. [#18252](url) chi v5.4.0, CVE fix · green, bot-approved · closes a critical issue
2. [#18234](url) namespaceSelector skipped when the namespace cannot load · green · touches pkg/cel
(at most 7, one line each: what it does, then why it is here)

**Blocked**
- [#18613](url) E2E gate hardening · red CI
(at most 4, one line each, the cause named; leave the section out if none)

**Heads-up**
- one line, only if something affects the order: a failing gate, two PRs editing the same file
```

- Dependabot PRs get one line in total ("5 Dependabot bumps, 3 green"), never a line each.
- Never write what you did not check, how you built the list, or that anything was skipped. If the fetch fails, one
  line: "Could not build the digest: <reason>".
- No ready PRs: "Nothing is waiting for review."
