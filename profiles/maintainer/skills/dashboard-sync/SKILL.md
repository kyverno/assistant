---
name: dashboard-sync
description: "Push what was just presented, and every confirmed action, to the maintainer's dashboard tab with update_dashboard."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
---

# dashboard-sync Skill

The maintainer's dashboard (`hermes -p kyverno dashboard`, Kyverno tab) shows
queues, triage results, Discussions and confirmed actions as linked items with
verdicts and related PRs/issues, so nothing has to be scrolled back to or
copy-pasted from the session. The other skills call this one at the points they
name. `update_dashboard`'s own description carries the field shapes.

## When to Use

- Right after presenting a PR queue, review brief, stale-PR list, issue triage,
  or Discussions summary.
- Right after a GitHub write the maintainer confirmed and that landed
  (approve, request changes, comment, label, nudge, close, assign, raise an
  issue), and after a defer.

## Procedure: push a presentation

Write the answer first, then push. Keep the call small — it costs tokens.

1. `section: "items"` — only items that need a decision, ranked by how much they need
   the maintainer, at most about 20 (a 165-PR sweep sends its top ~20). Per item: `key`,
   `title`, `url`, `status`, `verdict` (`action` + `reason`), `needs_you`, and `related`
   for every related PR/issue — plural, competing PRs included. Add state chips or
   `threads` only when you already have them, and `draft` when a comment awaits
   confirmation. Same verdict and reasons as the answer, never raw fetch output.
   Skip items unchanged since the last push.
2. `section: "views"` — the ordered list the maintainer saw, items sent first:

   | Presentation | `name` | `groups` / `entries[].group` |
   |---|---|---|
   | PR queue | `queue` | one group per tier, `tier` set on each entry |
   | Stale / author-blocked PRs | `stale` (`stale-<milestone>` when scoped) | the verdict: rescue, decide, close |
   | Issue triage | `triage` | priority bands, most urgent first |
   | Discussions | `discussions` | needs a decision, active, informational |

   A single-PR brief sends items only.
3. `section: "session"` — `focus`, `milestone`, `milestone_due`, `gate`, `counts`, only
   when they changed.
4. End the reply with one line, e.g. "Added 12 items to your dashboard (Kyverno tab) —
   open it to review."

## Procedure: log an action

Only after the write landed, never for a draft:

1. `section: "actions"` — `key`, a one-word `action` (approved, commented,
   nudged, closed, labeled, deferred), a one-line `summary`, and `link` to the
   comment, review, or issue the write produced.
2. Re-send the item: `draft` emptied, `verdict.action` set to `done` when the
   decision resolved it. No closing line beyond the confirmation already given.

## Pitfalls

- A confirmed action is logged once per write. Re-sending the same one updates
  it in place.
- `related` states each edge from this item's side (`closed-by`, `supersedes`,
  `competes`); send one side — the reverse is derived.
- If `update_dashboard` fails, finish the answer and say so in one line.
- Mention the push in a short clause ("on your dashboard"), not a paragraph.

## Verification

- After a presentation, the Kyverno tab lists every item named in the answer
  with the same verdict.
- After a confirmed action, the Activity tab shows it once, with a link.
