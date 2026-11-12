# What kyverno-assistant can do

A tour of the assistant's skills with real example prompts. Everything shown
here maps directly to a skill's actual procedure — see the linked
`skills/*/SKILL.md` for the full tool-by-tool detail behind each example.

## Building a merge-sequence recommendation

> **You:** what's my PR queue

First asks what today's focus is (a milestone, author, area, or fork PRs
needing workflow approval) — never defaults to "all PRs" or oldest-first.
Then fetches every matching PR in one batched call (human and Dependabot
together), builds the real dependency graph, and returns **tiers** — PRs
structurally free to review now, then PRs that must wait:

> **Tier 1**
> 1. **#17860** — admission-controller crash (`fatal error: concurrent map
>    writes`), `CRITICAL` by its closing issue, MEMBER author,
>    CodeRabbit-approved. Ranked first: severity outranks everything below it.
> 2. **#17857** — real 1.19 regression, cherry-pick requested (`HIGH`).
> 3. **#17831** / **#17838** — same author, same file, routine CLI fix;
>    presented together as one cluster rather than two separate positions.
>
> **Tier 2** (waits on Tier 1's generated-file/stacked-branch edges)
> 4. **#17669** — generated-input PR; #17000/#17540/#17770/#17778 must
>    follow it for that reason, stated explicitly, not guessed at.

A cycle (two PRs each needing the other first) or two PRs closing the same
issue gets named explicitly as `unresolved`, never silently ordered. Within
a tier, ordering follows a strict ladder — closing-issue severity, then
milestone proximity, then CodeRabbit-approved-with-zero-unresolved-threads,
then author association (MEMBER > CONTRIBUTOR > FIRST_TIME_CONTRIBUTOR),
then size as a pure tiebreak — and the reason is always named, never a bare
position. A PR flagged `CHANGES_REQUESTED` by CodeRabbit never lands in an
"approve now" grouping regardless of how urgent its milestone is — readiness
and priority are kept as separate axes on purpose.

## Explaining a specific PR

> **You:** explain PR #4181

Short by default — what it changes (plain language, not a diff dump), the
issue it closes, and Copilot/CodeRabbit's existing verdicts, synthesized
per their actual division of labor (CodeRabbit: security/lint/codegen
freshness; Copilot: logic/architecture/cross-file impact).

> **You:** explain PR #4181, and check if anything's risky or being
> discussed in Slack or Discussions

Same brief, plus: which review threads are still unresolved and who owes a
response, post-merge risk (does it touch `pkg/engine`/`pkg/cel`/other
surface only the post-merge suites exercise, any real incident history for
that package cited by PR and date, and is an `e2e-failure` issue currently
open for the target branch — stated independently of `ready-for-review`,
never conflated with it), a suggested action (approve / request changes /
wait on CI / needs author to resolve threads), and whatever the
maintainers' Slack channel or GitHub Discussions say about it in the window
checked — cited by message/thread, not a bare "yes/no." Checking either one
and finding nothing is reported as "not found there," not "never discussed."

## Dependabot and duplicate-effort checks

> **You:** which Dependabot PRs should I review and merge?

Looks at every open Dependabot PR — labelled or not — and gives each exactly one
verdict, with timing: **merge now** (patch/minor, clean, Copilot approving, no risky
call sites), **fix first** (a named blocker such as a conflict or a Copilot finding
that needs a code change), **your review** (major bumps, risky call sites, or a
Kubernetes/`kyverno/api` bump that needs regenerated code), **wait**, or
**close/ignore** (a duplicate, or a bump you already decided against). Each one cites
the real call sites in the repo, the release notes, Copilot's findings, the failing
check if any, what it came after, and which other open PRs it blocks or waits on.
Security fixes sort first. It never merges — that click is yours.


> **You:** does #4172 need anything from me right now?

Reads Copilot's actual review body and the failing check names rather than
reporting the bare `needs-review` label — e.g. "Copilot flagged the
changelog entry as missing" or "the `Go vet` check is failing," never a
generic "not approved yet."

> **You:** explain #4190

If another open PR is also configured to close the same issue, it says so —
`closed_by_pull_requests` surfaces duplicate effort before you spend a
review on either one without knowing.

## PRs waiting on their author

> **You:** which stale PRs should I close or reassign?

Doesn't just flag "stale." Checks the linked issue for each: already fixed by
a different, merged PR (close, citing it), already has another open PR
competing for it (close in favor of that one), or genuinely still wanted
(reassign, with the real path owners named from CODEOWNERS). Capped and
ranked by real severity, never a flood of everything stale at once.

## Triaging issues

> **You:** tell me about issue #4512

Identifies the likely subsystem and checks it against the real code — the
package's own docs, or a direct code search — before classifying, rather
than reading the issue's description back as if it were already verified.
If more than one PR claims to close it, both show up as a real finding, not
silently picked between. If the real code suggests this is actually
security-relevant, says so plainly and suggests the real label — it won't
sit as an ordinary bug just because nobody's looked closely yet.

> **You:** give me the 10 newest triage-queue issues

Same grounded read, as a batch, plus whatever Discussion or Slack context
exists for each one.

## Deciding on an issue

> **You:** mark #4512 a duplicate of #4480

Drafts the closing comment, shows you the exact text, then posts it and
closes together once you confirm.

> **You:** raise an issue for the thing we were just discussing

Works from a direct request, or from something the agent itself noticed in
Slack or a Discussion with nothing tracking it yet. Either way, drafts the
issue and, if there's a source thread, a link-back reply to it — shown
together before anything posts.

## Cross-referencing and CI breaks

> **You:** is anything else related to #4181?

Searches issues and PRs for mentions that aren't formally linked, and says
plainly when it finds nothing rather than fabricating a connection.

> **You:** CI broke on main, what's affected?

Looks up the actual failing workflow run and names which currently-open PRs
overlap it by file path — not "some PRs might be affected."

## Slack

> **You:** post the merge sequence to the maintainers channel

Drafts the message, shows it to you, posts only once you confirm.

> **You:** did anyone already ask about #4172 in Slack?

Scans a bounded window of channel history and text-matches against the PR
number/title/URL — and names the window it checked, so "not found" reads as
"not in the last N days," not "never discussed." If a PTAL-shaped thread
turns up, it can draft a reply and post it into that same thread once you
confirm.

## GitHub Discussions

> **You:** anything new in Discussions I should look at?

Lists open discussions, checked individually for actual state (there's no
open/closed filter on the listing itself) rather than assuming every result
is still active.

> **You:** answer the discussion asking about X

Reads the whole thread first, cross-references the codebase if the
question needs it (citing what it found, not guessing), then drafts a
reply and shows it to you — posting only once you confirm, since a
discussion answer is visible to the whole community, not just you.

## Remembering things across sessions

> **You:** explain PR #4200

Post-merge risk isn't just "no path-to-suite mapping exists" — if the
package combination it touches has broken the post-merge suite before,
that's cited by PR number and date, not just flagged as a blind spot.

> **You:** is anything else related to #4181?

Checks closed and rejected PRs too, with why they didn't land — not just
what's currently open.

> **You:** remember that dependency bumps to logging libraries need extra
> scrutiny — they've caused problems before

Kept as a durable fact, retrieved automatically the next time a similar PR
comes up — not something you have to repeat every session.

It also picks up on real, repeated patterns on its own — a contributor who
consistently needs a second round on generated-file changes, a standing
policy stated once in Slack — and factors that in before drafting a review
brief or a discussion reply, without being asked to remember it.

## A standing review digest, without asking every time

A scheduled job ships with the profile (`cron/jobs.json`) — weekday
mornings, it builds the merge sequence and posts a short digest to your
Slack channel on its own: what's new since the last digest, and the
current review order. It arrives paused; `hermes cron resume
review-digest` turns it on, and `hermes cron edit
review-digest --schedule "..."` changes the timing. Each run
remembers its own last output, so the digest says what changed rather than
repeating the whole list every morning.

Two more scheduled jobs ship alongside it, also paused by default and
silent unless they find something: one checks every few days for PRs that
closed unmerged or post-merge breakage that just happened, and remembers
the reason when one's known; the other periodically compresses what's
been remembered so far into more useful, distilled patterns.

## Taking action, with your own credentials

> **You:** label #4172 as `kind/dependency`

Checks the repo's real current label set first — never invents or assumes
a name exists.

> **You:** approve #4188

Confirms current PR state before acting, then reports back specifically
what happened and to which PR.

> **You:** rebase #4190

Brings the branch up to date with its base — and says plainly that this is
a merge-update, not a true git rebase (GitHub's API doesn't offer one),
warning up front that the PR touches codegen inputs so the update alone
won't regenerate now-stale generated files.

## Seeing results on the dashboard

Everything the agent presents — PR queue, stale PRs, issue triage, Discussions — and
every action you confirm is also written to the Maintainer tab of `hermes -p kyverno
dashboard`: linked items with verdicts, related PRs/issues, drafted comments, and an
activity log. Use it to keep the full picture in front of you instead of scrolling the
chat. See the README's "The dashboard".

## Boundaries, by design

- **Never merges anything.** No merge tool exists in its toolset at all —
  enforced in three independent layers (server-side exclusion, an allowlist,
  a hook backstop), not a rule it simply follows.
- **Never does a true git rebase** (no history rewriting) — a merge-update
  via the GitHub API is the closest available action, and it says so.
- **Never commits or pushes code** — including a fix it just diagnosed and
  described for you. It will describe the fix and ask; a human applies it.
- **Never polls or watches for events** — no webhook, no "check every N
  minutes for a change." The one scheduled thing it does (the review
  digest above) runs on a fixed clock and only ever posts a digest;
  everything else — CI status, Slack mentions, post-merge risk — is
  checked because you asked in that turn.
- **One repo, one maintainer per installed instance** — point a separate
  install at each repo/maintainer pair rather than one instance serving many.

## How it works, briefly

- Installs as a **Hermes profile**; you talk to it on the CLI or in Slack.
  No server to run, no webhook receiver — nothing runs when nobody's asking
  it anything (aside from Hermes' own gateway process, which just listens
  for messages).
- Uses **your own GitHub token**, scoped read plus limited write — every
  action it takes is attributable to you.
- **Never hardcodes** repo conventions — labels and CODEOWNERS are
  re-resolved live every session, because they drift and because the same
  profile may point at a different repo later.
- Approximates "what would this break" via repo-wide text search rather
  than a real call graph — no extra infrastructure to run, and catches the
  large majority of real conflicts in practice (file-overlap plus
  generated/interface classification).
- The Slack chat interface (mentions/DMs) and the Slack *tool* (reading and
  posting to the maintainers channel) are two independent integrations that
  happen to share a bot token — see `docs/architecture.md` if you're
  debugging one without the other.
- Remembers durably across sessions — current focus and working style in a
  compact always-loaded profile, and an accumulating knowledge store for
  things like past incidents, rejected PRs, and contributor patterns that
  would outgrow that. Live GitHub/Slack state (open PRs, current labels,
  recent messages) is always looked up fresh, never memorized — it would
  go stale the moment anything changed.
