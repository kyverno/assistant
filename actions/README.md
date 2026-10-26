# Shared CI actions

Composite GitHub Actions that any organisation can use, with no assistant, container or model involved. Each
action is deterministic, reads its repo-specific settings from a config file in the **caller's** repository, and
defaults to a dry run.

| Action | What it does | Status |
| --- | --- | --- |
| [`pr-management`](pr-management/README.md) | Path labels, readiness classification, stale-author handling, label sync | Ported from kyverno/kyverno#18275 |
| `flaky-tests` | Detect, track and quarantine flaky tests from workflow history | Planned |
| `security-triage` | Summarise and route code-scanning, secret-scanning and Dependabot alerts | Planned |
| `vuln-scan` | Scan dependencies and images, de-duplicate findings, open or update tracking issues | Planned |

Conventions every action follows are in [`docs/generalisation.md`](../docs/generalisation.md#conventions-for-actions).
Ready-to-copy caller workflows and config are in [`examples/ci/`](../examples/ci/).
