# Shared issue management

Reusable composite action that keeps an issue tracker tidy without closing real work. Runs on `ubuntu-latest` with
Python 3.9+ and `gh`; no packages. The script is loaded from `GITHUB_ACTION_PATH`, not the consuming checkout.

## Rules

| Rule | When | Action |
| --- | --- | --- |
| possibly resolved | A merged PR mentions the issue without closing it, `resolved_grace_days` passed, and no human commented since the merge | Adds `possibly-resolved` and comments. Never closes. |
| waiting on reporter | The issue has a `needs_info_labels` label and its author has not replied since it was applied | After `warning_days`, adds `stale` and comments. After `close_days` more, closes as not planned. **The only rule that closes.** |
| idle untriaged | The issue has `triage_label`, no label starting with `priority_prefix`, and no human activity for `idle_days` | Adds `stale` and comments. Never closes. **Off unless `triage_label` is set.** |

A human comment after `stale` or `possibly-resolved` removes it, even on an exempt issue. Issues with an
`exempt_labels` label or a milestone are never labelled or closed. Bot comments never count as activity, and a
maintainer's comment is not a reply from the reporter. The action keeps no state: labels and the issue timeline
are the state.

## Interface

| Input | Default | Meaning |
| --- | --- | --- |
| `config` | `.github/issue-management.json` | Repository-specific JSON; the file and every key are optional |
| `dry-run` | `'true'` | Exactly `'true'` or `'false'`; preview never mutates GitHub |
| `max-actions` | `'30'` | With `dry-run: 'false'`, the most issues one run changes; the rest are listed as skipped |

Supply `GH_TOKEN` in the caller step's environment. It needs `issues: write` and `contents: read`. The report goes
to the job summary.

## Configuration

Unknown keys, non-integer days and comment templates with unknown `{fields}` fail the run.

| Key | Default |
| --- | --- |
| `stale_label` / `resolved_label` | `stale` / `possibly-resolved` (created if missing when live) |
| `triage_label` / `priority_prefix` | empty: idle rule off |
| `needs_info_labels` | `["needs-info"]` |
| `exempt_labels` | `["no-stale", "hold", "security"]` |
| `warning_days` / `close_days` | `14` / `14` |
| `idle_days` / `resolved_grace_days` | `180` / `3` |
| `warning_comment`, `close_comment`, `resolved_comment`, `idle_comment` | generic text; `{days}` or `{pr}` allowed |

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .github/actions/issue-management/tests -v
```
