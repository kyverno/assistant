# Shared issue management

Reusable composite action that keeps an issue tracker tidy without closing real work. Runs on `ubuntu-latest` with
Python 3.9+ and `gh`; no packages. The script is loaded from `GITHUB_ACTION_PATH`, not the consuming checkout.

## Rules

| Rule | When | Action |
| --- | --- | --- |
| possibly resolved (**off** until `resolved_label` is set) | A merged PR mentions the issue without closing it, `resolved_grace_days` passed, no human commented since the merge, no open PR mentions it either, and the issue is in none of the `queue_labels` | Adds the `resolved_label` and comments. Never closes. |
| waiting on reporter | The issue has a `needs_info_labels` label and its author has not replied since it was applied | After `warning_days` of quiet, adds `stale-issue` and posts a warning. Once that warning is `close_days` old and the label is still there, closes as not planned. **The only rule that closes.** |

- A human comment after `stale-issue` removes it. It removes `possibly-resolved` too, unless
  `resolved_clears_on_comment` is `false`. A reply from the reporter removes the needs-info label, so a later request
  starts a fresh clock. A maintainer's comment is not a reply, but it does restart the quiet period before a warning.
- Issues with an `exempt_labels` label or a milestone are never labelled or closed, and lose `stale-issue`, so lifting
  the exemption later cannot close on an old clock.
- An issue already in one of the `queue_labels` is not given `possibly-resolved`: a person put it in that queue.
- **A close needs proof of a warning.** The action only closes if the bot's own warning comment for the current
  request exists and is `close_days` old. A `stale-issue` label set by hand is not enough, one put back after the warning
  does not count, and a maintainer who removes the label after the warning is respected.
- **A close re-reads the live issue first** and is abandoned (and logged) if it is no longer open, no longer waiting
  and stale, has become exempt, or has changed in any way since the run read it.
- Bot comments never count as activity.

## Queue report

With `queue_labels` set, the report ends with a **Queues** section: for each queue label, how many open issues carry
it and the `digest_limit` least recently updated, plus how many open issues are in none of the queues. It labels and
comments on nothing. It is for whoever works the queues; the days are since the last update, which includes bot
activity, because a search can give that without reading each issue.

## How it works

1. **Candidates come from searches, not a scan of every issue**: issues carrying a managed or needs-info label, and
   issues updated within `resolved_window_days` that a merged PR mentions (a light first look rules most out, so they
   are not read at all). Exempt issues are left out of the second search.
2. **Each candidate is read once.** Comments and label events arrive newest first, 100 at a time, and are paged
   backwards only as far as a decision needs: to the event that applied each relevant label, to the comments since
   then, and to the newest human comment. Paging stops at a cursor that does not move, and a malformed page is an
   error.
3. **Decisions are pure and stateless.** Labels, the timeline and hidden markers (`<!-- issue-management: … -->`) in
   the bot's own comments are the state. Only a Bot's comment counts as a marker, so a user cannot plant one. A step
   already marked for the same state is not repeated.
4. **One guard makes every write.** In a dry run it prints and does nothing; otherwise it waits a second before each
   write, to stay under GitHub's secondary rate limit. Reads retry; writes never do, so a comment cannot double. A
   GraphQL answer with `errors` or no `data` is rejected.
5. **One failing issue does not stop the rest.** Failures are counted, logged as `::error::`, and make the run exit 1
   at the end.

A preview lists the comments a live run would post. If something cannot be decided, such as a label whose event is no
longer in the history, the issue is skipped and the report says why.

## Interface

| Input | Default | Meaning |
| --- | --- | --- |
| `config` | `.github/issue-management.json` | Repository-specific JSON; the file and every key are optional |
| `dry-run` | `'true'` | Exactly `'true'` or `'false'`; preview never mutates GitHub |
| `max-actions` | `'30'` | With `dry-run: 'false'`, the most issues one run changes; the rest are listed as skipped |

Labels must exist: the report lists any configured label the repository lacks, and a live run skips an issue whose
label is missing. Supply `GH_TOKEN` in the caller step's environment. It needs `issues: write` and `contents: read`. The report goes
to the job summary.

## Configuration

Unknown keys, non-integer days, label names containing a double quote (they go into searches) and comment templates
with unknown `{fields}` fail the run.

| Key | Default |
| --- | --- |
| `stale_label` | `stale-issue`. Created by the Sync labels workflow, never by this action |
| `resolved_label` | `null`: the possibly-resolved rule is off. Name a label (for example `possibly-resolved`, or a queue label you already have) to turn it on |
| `resolved_clears_on_comment` | `true`. `false` keeps `resolved_label` when someone comments, for a label that is a review queue a person clears |
| `queue_labels` | `[]`. The repository's review-queue labels |
| `needs_info_labels` | `["needs-info"]` |
| `exempt_labels` | `["no-stale", "hold", "security"]` |
| `warning_days` / `close_days` | `14` / `14` |
| `resolved_grace_days` / `resolved_window_days` | `3` / `30`. A mention only makes an issue a candidate while the issue was updated this recently |
| `digest_limit` | `10` issues per queue in the report; `0` lists none |
| `warning_comment`, `close_comment`, `resolved_comment` | generic text; `{days}`, `{pr}` and `{label}` allowed where they make sense |

### Example: a repository that sorts issues into review queues by label

```json
{
  "resolved_label": "triage:verify-closure",
  "resolved_clears_on_comment": false,
  "queue_labels": [
    "triage:critical-review", "triage:needs-repro", "triage:verify-closure",
    "triage:scope-review", "triage:decision"
  ],
  "needs_info_labels": ["needs-info"],
  "exempt_labels": ["no-stale", "hold", "security", "release-critical", "release-high"]
}
```

New issues that a merged PR mentions get `triage:verify-closure`. Issues a person already queued are left alone, and
no comment ever removes a queue label.

## Limits

- Search returns at most 1000 issues; the run warns when a search hits that.
- `possibly-resolved` only sees mentions on issues updated within `resolved_window_days`, and an issue mentioned by
  more than 100 PRs is skipped with a note.
- Search results can lag a little behind recent changes.

`stale-issue` is deliberately not `stale`: the PR automation owns `stale`, and an issue and a PR should not share a
label. An issue that still carries a plain `stale` label from an older bot is ignored.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .github/actions/issue-management/tests -v
```
