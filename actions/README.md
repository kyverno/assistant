# Shared CI actions

Composite GitHub Actions that any organisation can use, with no assistant, container or model involved. Each
action is deterministic, reads its repo-specific settings from a config file in the **caller's** repository, and
defaults to a dry run.

| Action | What it does |
| --- | --- |
| [`pr-management`](pr-management/README.md) | Path labels, readiness classification, stale-author handling, label sync (ported from kyverno/kyverno#18275) |

To adopt it, see [`docs/adopting-actions.md`](../docs/adopting-actions.md): one script opens the PR. Caller
workflows and config templates are in [`examples/ci/`](../examples/ci/).
