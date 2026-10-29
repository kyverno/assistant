---
name: grow
description: "The path from first PR to reviewer: what the contributor has done, what the project's ladder asks for, what to work on next to deepen an area or widen it, and the evidence to bring when asking for a role."
version: 0.1.0
author: Suhaani Agarwal
license: Apache-2.0
platforms: [linux, macos, windows]
---

# grow

## When to Use

- After a PR merges; "what should I do next?"; "how do I become a contributor/reviewer?"; "am I ready to ask?"

## Steps

1. `journey_summary`: `stage`, merged PRs here and org-wide, `known_areas` (and how many PRs each),
   `reviewed_areas`, reviews and comments on others' work, `review_opportunities`, and `ladder`. Read `ladder.criteria_status`: if it says unverified or changed,
   tell the contributor to re-read the source (`ladder.source`) before relying on the numbers.
2. **Where they stand.** For the contributor role report the merged-PR count against the requirement and the
   collaboration items GitHub shows. State what only they can confirm (Slack, meeting, mailing list, blog
   posts, Slack support) and ask; record answers with `journey_self_report`. Do not say they qualify: the
   project decides, and reviewers are nominated by a maintainer.
3. **After a merge: a short retrospective.** What review taught (from `my_pr_state` history), what they
   now know. Save with `journey_note` kind `retro`.
4. **Next steps, one level up each time.** Offer 2 or 3 options:
   - **Deepen**: another issue in the same area (`find_issues` with the area) that reuses what they learned;
   - **Widen**: an adjacent package: `explain_path` on their area, then the folders it imports or that import
     it, and issues there;
   - **Review**: the reviewer step asks them to actively review others' code. Only when `stage` is not
     `first_time`: offer the PRs in `review_opportunities` (open, in paths they know) and help them review one
     with `review-loop`; they may post the comment, as any contributor can;
   - **Teach**: docs, a tutorial or a blog post about what they learned also counts as a contribution.
   Say why each fits them, what it will teach, and what the step after it could be.
5. **Evidence pack** when they decide to ask for a role: the list of merged PRs, reviews and collaboration
   items with links, and the draft text of the PR the source asks for (for example adding themselves to
   CONTRIBUTORS.md). The contributor opens the PR and asks a sponsor; you never contact anyone.

## Verification

- It states the criteria's source and whether they are verified, and never says the contributor qualifies.
- It asks about Slack/meeting/mailing list instead of assuming them.
- Next-step options are tied to the contributor's actual areas from `journey_summary`.
- Nothing is sent to anyone; the output is drafts and links.
