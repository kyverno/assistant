# Shared CI actions

Composite GitHub Actions that any organisation can use, with no assistant, container or model involved. Each
action is deterministic, reads its repo-specific settings from a config file in the **caller's** repository, and
defaults to a dry run.

| Action | What it does |
| --- | --- |
| [`pr-management`](pr-management/README.md) | Path labels, readiness classification, stale-author handling, label sync (ported from kyverno/kyverno#18275) |
| [`issue-management`](issue-management/README.md) | Labels issues a merged PR may have resolved, warns then closes issues waiting on the reporter, flags idle untriaged issues; only the reporter rule closes |
| [`ci-health`](ci-health/README.md) | Read-only report on one workflow's recent runs: root causes shared across jobs, persistent and flaky jobs, commits since the last green run, cost |

Back to the [main README](../../README.md#ci-automation-for-your-repositories). To adopt it, see [`docs/adopting-actions.md`](../../docs/adopting-actions.md): one script opens the PR. Caller
workflows and config templates are in [`adopt-ci-templates/`](../../adopt-ci-templates/).
