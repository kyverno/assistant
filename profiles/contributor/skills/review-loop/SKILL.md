---
name: review-loop
description: "After the PR is open: explain every review comment and failing check, say which look right and which deserve pushback with evidence, keep the PR moving, and draft reviews of other people's PRs for the contributor to post."
version: 0.1.0
author: Suhaani Agarwal
license: MIT
platforms: [linux, macos, windows]
---

# review-loop

## When to Use

- "What do these comments mean?", "why is CI red?", "my PR is stuck", "is this PR ready?", "help me review #N".

## Your own PR

1. `my_pr_state`. Start with `needs_from_author` (what blocks the PR) and `attention_labels`
   (`needs-author-action`, `merge-conflicts`, `stale`, `workflow-approval-required`).
2. **Threads.** Go through `unresolved_threads` grouped as maintainer, other human, bot. For each: restate
   what is being asked in one line, with the link, and classify:
   - **right**: say why (open the code at that line) and what the change would be;
   - **push back with evidence**: the project's REVIEW.md says bots are sometimes wrong. Give the evidence
     (code, test, doc, an earlier PR) so the contributor can reply; never assert it without checking;
   - **unclear**: say what to ask.
   Maintainer comments weigh more than bot comments; start there.
3. **Checks.** For each `failing_checks` entry: open the run (`actions_get`, `get_job_logs`), say why it
   failed, and whether it is `also_failing_on_base_branch` (then not caused by the change: say so and that a
   re-run or a wait is the answer). `infra_checks_failing` are gates, not the author's to fix.
4. **Conflicts and staleness.** Explain what changed on the base branch. Fix conflicts in the sandbox clone;
   the contributor pushes. Warn when `days_since_activity` is high or `stale` is set, and what to say.
5. **Drafts.** Offer bullet points each reply should cover. The contributor writes and posts replies.
6. `journey_note` kind `review_feedback` for a lesson worth keeping ("reviewers here want table tests").

## Reviewing someone else's PR

Run the same checks as `make-the-change` step 6, from the outside: `pull_request_read` for the diff, files,
checks and existing reviews; `knowledge_for` and `explain_path` for the touched paths; read the changed code;
check the PR against REVIEW.md and the readiness file. Write a draft: what the change does, what you
verified, concrete findings with file and line links, questions, and what you did not check. The
contributor edits it and decides; post it as a PR comment (`add_issue_comment`) only after they have seen
the exact text and said yes. The review is theirs, so keep it in their voice and never approve or request
changes for them. Build someone else's code only in a separate sandbox that holds no credentials.

## Verification

- It leads with what blocks the PR and cites the thread or run link for every item.
- A bot comment is not accepted or rejected without reading the code it refers to.
- A failing check that fails on the base branch is reported as such.
- It offers points, never reply text, and never posts to a review thread.
- A review of someone else's PR is shown to the contributor first and posted only after their yes.
