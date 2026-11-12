# Maintainer Assistant

**An AI assistant that works your pull-request queue and issue backlog the way a careful co-maintainer would: it finds
what is ready, works out the order, explains every call with a link, and does the paperwork. It cannot merge.**

It runs on your own machine inside an [OpenShell](https://github.com/NVIDIA/OpenShell) sandbox, so the agent that
reads strangers' issues never holds your GitHub token and can only reach the few hosts it needs. It is built on
[Hermes](https://hermes-agent.nousresearch.com) and works with any repository that follows a few conventions, listed
below. Kyverno is the first organisation to use it.

> 🧭 **Where this phase is.** Data classification and visibility for maintainers: knowing what is in the queue, how it
> relates, and what to do first. Acting on GitHub is supported, but only when you ask. See the
> [roadmap](#roadmap).

## Contents

[What it does](#what-it-does-for-a-maintainer) · [How it thinks](#how-it-thinks) ·
[Prepare your repository](#prepare-your-repository) · [Set it up](#set-it-up) · [Using it](#using-it) ·
[Protected by OpenShell](#protected-by-openshell) · [Key features](#key-features) ·
[What is verified, and the limits](#what-is-verified-and-the-limits) · [Roadmap](#roadmap) · [Documentation](#documentation)

## What it does for a maintainer

A flood of PRs and issues makes the same few things slow. Here is what the assistant takes off your plate:

| The problem | What it does |
|---|---|
| "Which PRs can I merge today?" | Fetches the whole candidate set, drops what is not ready, and gives an order with a reason per PR |
| Merging one PR forces rebases on others | Flags PRs that touch files another PR changes, so you see the rebase coming before you merge |
| PRs that depend on each other | Finds stacked branches, "depends on #N" references, and generated-file input/output pairs, and orders them |
| A red branch gate blocks everything | States it as a fact on every PR it affects, and leaves the bypass decision to you |
| Dependabot PRs pile up | Gives each one a verdict (merge now, fix first, your review, wait, close) based on who calls the dependency |
| Issues need triage | Classifies from the real code, finds duplicates and competing PRs, and says how far along each fix is |
| Stale and abandoned PRs | Recommends close, reassign, or take over the branch, with the reason from the linked issue |
| GitHub Discussions | Reads the thread and drafts a reply; nothing is posted until you say so |
| "What did we decide last time?" | Remembers past incidents, rejected PRs and your working style between sessions |

Everything it says is linked to the PR, issue, file or run it comes from. When it looked and found nothing, it says
"not found there" instead of guessing.

## How it thinks

The idea behind it: **code computes the facts, the model makes the judgment, and you make the decision.** Anything that
can be computed exactly (a dependency, a file overlap, a gate) is computed by a deterministic tool, so the model does
not guess it. The model then explains and ranks, citing what the tools returned.

### What the queue is built from

| Signal | Where it comes from | What it tells you |
|---|---|---|
| Stacked branches | A PR's base branch is another candidate's head branch | Merge the base first |
| Generated-file order | One PR changes a generated file's *input*, another changes its *output* | The input has to land first, or the output goes stale |
| Explicit references | "Depends on #N", "Blocked by #N", "Stacks on #N", "Parent: #N" in the PR body | Order, including references to PRs outside the set (it fetches their state) |
| Competing PRs | Two PRs close the same issue | Duplicate effort: flagged for you, not ordered |
| Same-file overlap | Two PRs touch one file | A likely rebase conflict; it can compare the real diff hunks to tell you if the *lines* collide |
| Same-package overlap | Same directory, different files | Cannot conflict in git, but worth reviewing together |
| Rebase flag | A PR touches files an earlier-ranked PR changes | It will need updating after that one merges |
| Branch gate | An open gate-failure issue for the PR's base branch | Merging is blocked on that branch whatever the files; review is not |
| File class | Your organisation pack: generated, API surface, critical, test-only | How risky the change is after merge |
| Past incidents | Its memory of which package pairs broke the post-merge suite before | A specific, citable risk |

Cycles are detected and handed to you by number. Within a group of PRs with no hard dependency between them, it ranks
with a fixed ladder and says which rung decided each position:

1. Related PRs stay together (same file, same package, same issue), explained once.
2. **Severity of the closing issue**, judged from its content, never guessed: critical (crash, outage, data loss,
   security), high (regression, user-facing break), medium, low.
3. Release-urgency labels on the closing issue, then how close the milestone is.
4. Bot approval and unresolved review threads.
5. Author's relationship to the project, then size as a pure tiebreak.

Anything you said this session, or the maintainers' channel said, overrides the ladder. **Readiness is a separate axis
from priority:** a high-severity PR with changes requested or a real CI failure is never listed under "approve now";
the blocker is named instead.

### What an answer looks like

An illustrative queue answer (numbers invented):

```
Queue for milestone 1.20: 14 of 31 open PRs, oldest first within each group

Group 1: no hard dependency between these
  #18102  split admission handlers      closes #17990 (CRITICAL: crash on startup)
          ✓ checks green · bot-approved · 0 open threads
          ⚠ #18110 is stacked on this one: merge #18102 first
  #18077  fix cleanup TTL parsing       closes #17811 (HIGH: regression in 1.19)
          ✓ ready · touches pkg/cleanup, also changed by #18090 → review together
Group 2
  #18110  stacked on #18102             ⏳ waits for #18102
  #18090  rename policy fields          ⚠ rebase_flag: shares api/v1/policy_types.go with #18102
          ⚠ both change a generated-file input: no safe order, your call
Gate: main is red (issue #18121). Every PR above is blocked on merge until it clears.
```

### What it will not pretend

It is a structural tool. It orders PRs by what it can prove (files, branches, references), not by whether one change
semantically breaks another. It says so when asked. It does not merge and it does not apply a gate-bypass label. It writes to GitHub only when you ask, and every
comment, review or reply is drafted and shown to you before it is posted.

## Prepare your repository

The assistant is only as sharp as what your repository tells it. Three things make it effective; the first one comes
**before** you install anything.

### 1. Adopt the shared CI and labels first

The queue and the stale-PR skills read labels that a small set of shared GitHub Actions maintains: `ready-for-review`,
`needs-author-action`, `workflow-approval-required`, `stale`, `merge-conflicts`, and path labels from your
`.github/labels.yml`. A repository that already runs them hands the assistant its signals for free. Without them it
still runs, but it has no readiness labels to go on and says so.

One person runs one command; it opens a PR in each repository you name:

```bash
git clone https://github.com/kyverno/assistant && cd assistant
gh auth login
scripts/adopt-ci.sh --reviewers @your-org/maintainers your-org/repo-a your-org/repo-b
```

| It adds | Purpose |
|---|---|
| `.github/workflows/pr-management.yaml`, `pr-labelling.yaml`, `sync-labels.yaml`, `check-pr-management.yaml` | Short callers of the shared workflows: readiness, stale, path labels, label sync, config check |
| `.github/pr-management.json` | Reviewer team, exempt labels, timings |
| `.github/pr-readiness-checks.yml` | Which checks block a PR, and which are not the author's to fix (DCO and CI come built in) |
| `.github/labels.yml` | Your label catalog and path rules |

Everything starts in **preview**: it prints what it would do and changes nothing, until an admin sets the repository
variable `PR_AUTOMATION_LIVE` to `true`. The actions need no model, no container and no secret of yours. There is also
an `issue-management` action (possibly-resolved and waiting-on-reporter handling; only the reporter rule closes
anything). Full steps and permissions: [`docs/adopting-actions.md`](docs/adopting-actions.md).

### 2. Write your organisation pack

Everything specific to your organisation lives in one small directory, never in code: `container/org/<your-org>/`.
`org.yaml` says which paths are generated, API surface, critical or test-only, whether you have a merge gate, and which
labels mean "ready". `facts.md` is optional prose in your own words. About ten lines get you started:

```yaml
name: acme
readiness:
  ready_labels: [ready-for-review]
  attention_labels: [needs-author-action]
maintainer:
  file_classes:
    generated: ["**/zz_generated.*.go", "config/crds/**/*.yaml"]
    critical:  ["pkg/engine/**", "pkg/webhooks/**"]
    test_only: ["*_test.go", "test/**"]
```

Without a pack the assistant still works: it reads every file as ordinary, mentions no gate, and tells you which
signals it is missing. A mistake in the pack is an error that names the key, never silently ignored.
Reference and every key: [`docs/organisation-pack.md`](docs/organisation-pack.md). Kyverno's own is in
[`container/org/kyverno/`](container/org/kyverno/).

### 3. Keep your own knowledge files accurate

It grounds its answers in what your repository says about itself, so the better those files are, the less it guesses:

| File | What the assistant uses it for |
|---|---|
| `CODEOWNERS` | Who owns a changed path; who to hand a stale PR to. Read live, last match wins, as GitHub does |
| `AGENTS.md` (root and per package), `ARCHITECTURE.md` | Grounding an issue or a PR in the real code before classifying it. It finds them with code search when it needs them |
| `.github/labels.yml` | What each label means (it reads the label *descriptions*, not just the names) |
| Your milestones | Release proximity in the ranking |

If a package has no doc, it says its knowledge there is only what the code, owners and history show.

### 4. Create a token

A fine-grained personal access token for the **one repository**, from your own account:
Contents **Read**, Pull requests **Read and write**, Issues **Read and write**, Checks **Read**, and Code scanning,
Dependabot and Secret scanning alerts **Read**; organisation Members **Read** if CODEOWNERS uses teams. No Contents
write, no Administration: GitHub itself then refuses to merge. Or let the launcher use your `gh` sign-in. The token is
handed to OpenShell and never enters the sandbox ([why that matters](#protected-by-openshell)).
Step by step: [`docs/setup.md`](docs/setup.md#1-create-a-github-token).

## Set it up

You need **Docker**. Verified on Apple Silicon Macs, and on Linux amd64 on a CI runner; other combinations are untested
([details](docs/getting-started.md)). One command, no clone, no folder to create:

```bash
curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh
```

It checks Docker, installs OpenShell if needed, and asks once: which assistant (choose **maintainer**), the repository
(`owner/repo`, there is no default), how to authorize GitHub (your `gh` sign-in, or paste the token), and how to sign in
to a model (a GitHub Copilot device-code login, or `--provider anthropic|openai|nvidia`). Then it opens the chat. Later,
`container/assistant` alone brings you back.

**Using your own organisation pack:** clone this repository, add `container/org/<your-org>/`, and build your image:

```bash
container/assistant --build --org your-org
```

The pack is baked into the image read-only. First builds take 15 to 30 minutes.

| To | Run |
|---|---|
| Come back, or open a second chat | `container/assistant` |
| Upgrade, keeping memory, history, sign-in and model | `container/assistant --upgrade` |
| See everything it contacted, and what was blocked | `openshell logs assistant-maint` |
| Stop it / delete it and its state | `openshell sandbox stop assistant-maint` / `openshell sandbox delete assistant-maint` |
| Run without OpenShell (less protection) | `container/assistant --no-openshell` |

> Sandboxes made before the rename (`kyverno-assistant`) keep working under their old name. Everyday use, data and
> troubleshooting: [`docs/usage.md`](docs/usage.md).

## Using it

Talk to it in the terminal. It is a conversation, so ask the way you would ask a colleague:

| Ask | You get |
|---|---|
| "What's my review queue for 1.20?" | Asks for a focus if you did not give one (a milestone, an author, an area), then the grouped, ordered queue |
| "Brief me on #1234" | Short by default: what it changes, why, review state. Deeper on request: threads, risk, suggested action |
| "Which Dependabot PRs can I merge today?" | A verdict per PR with the call sites, release notes and review findings behind it |
| "Triage the 10 oldest issues with no PR" | Class, severity, duplicates, competing PRs and progress, grounded in the code |
| "Which stale PRs should I close?" | Close, reassign or take over the branch, each with the issue state behind it |
| "Draft a reply to the discussion about X" | The draft, shown to you first |
| "Label #1234 area/cli and ask the author to fix DCO" | Applies the label you asked for, and shows you the comment draft before posting it |

Results also land on a read-only **Maintainer tab** at `http://127.0.0.1:9219/maintainer?profile=maintainer` (the
launcher prints the address), so you read the queue there instead of scrolling the chat. It also remembers your focus
and working style between sessions, plus past incidents and rejected PRs. What you ask for explicitly (a label, an
approval) it does and reports; anything with words in it (a comment, a review, a nudge, a Discussion reply) it drafts
and shows you first. Branch updates are GitHub's merge-update, not a git rebase, and it says so.

## Protected by OpenShell

An assistant that reads strangers' issues and PRs will, sooner or later, read text written to manipulate it. The design
answer is that **a tricked agent must still be unable to do harm.** [OpenShell](https://github.com/NVIDIA/OpenShell) is
the reason that holds: the strongest limits sit **outside the sandbox, where the agent cannot edit them.**

```
 your machine
 ┌─────────────────────────────────────────────────────────────────────────┐
 │  you ──► container/assistant ──► OpenShell gateway (outside the sandbox)│
 │                                   · holds your GitHub token             │
 │                                   · allowlist proxy: host, path, method,│
 │                                     and which program may call          │
 │                                   · swaps the real token in on the way  │
 │                                              │                          │
 │        ┌─────────────────────────────────────▼────────────────────┐     │
 │        │ SANDBOX: Hermes + the assistant + a terminal             │     │
 │        │  · holds only a placeholder where the token would be     │     │
 │        │  · code and safety hooks are read-only (Landlock)        │     │
 │        └──────────────────────────────────────────────────────────┘     │
 └─────────────────────────────────────────────────────────────────────────┘
                                   ▼
              GitHub (read APIs, the GitHub MCP server) · your model
```

### What the sandbox gives you

| Protection | How it works |
|---|---|
| **Your token never enters the sandbox** | The agent sees a meaningless placeholder. The gateway's proxy swaps in the real token only on requests to the listed GitHub endpoints, and only from the listed programs. A prompt-injected agent that is told to print its credentials prints the placeholder |
| **GitHub is read-only at the proxy** | On `api.github.com` REST is read-only and GraphQL mutations (including `mergePullRequest`) are refused by the proxy, not by a rule the agent could edit |
| **Every GitHub tool call is checked by name** | On the GitHub MCP server the proxy allows only the tools on the allowlist and refuses everything else, plus anything named like merge, delete or force-push, even when hidden in a batched request |
| **Deny by default network** | Anything not listed is blocked, per host, path and method, and per program: `git` and `curl` get read-only GitHub, the toolchain hosts are GET-only for the installers, and only Hermes' own Python may use the Copilot login |
| **Read-only code and hooks** | Landlock (a hard requirement) makes the system, Hermes' code and the baked safety hooks read-only. The agent can write only `/sandbox`, `/tmp` and Hermes' home |
| **A full audit trail** | `openshell logs assistant-maint` shows every request the sandbox made and what was allowed or denied |
| **Asking, not widening** | If a task needs a blocked host, the agent can propose the narrowest rule. You approve it on your own computer, and it cannot approve it itself |
| **Pushes only if you opt in** | `--push-to-fork` adds a separate credential for your own fork. A hook still refuses force pushes, deletes and pushes to main or release branches, and the network rule refuses every other repository |

### If the agent is tricked

Text in an issue says "ignore your instructions and..." These are the outcomes, by layer:

| It is told to | What stops it |
|---|---|
| Print or leak your token | It only ever holds a placeholder |
| Merge the PR | There is no merge tool; the proxy refuses merge tool names and GraphQL mutations; the token has no merge permission, so GitHub refuses |
| Send data to a host it picked | Network deny by default: the request is blocked and logged |
| Reach GitHub with the token through another program | The token is only swapped in for the listed programs |
| Rewrite its own safety hooks | `/opt/kyverno` is read-only to it |
| Delete the repo or force-push | Reads only at the proxy; pushes are opt-in and force pushes are refused |

### Honest limits

Layers that live inside the sandbox (the tool list in `config.yaml`, the hook that blocks merge-like tool names) can be
edited by a manipulated agent, because it has a terminal and file tools. The ones that matter, the token, the proxy and
the network allowlist, do not depend on any file inside it. A manipulated agent can still *say* something misleading, so
check the links it cites before you act on a recommendation. What was tested, and how:
[`docs/container.md`](docs/container.md). The full design: [`docs/openshell-architecture.md`](docs/openshell-architecture.md).

## Key features

| Feature | Status |
|---|---|
| Ordered merge queue with reasons, tiers, rebase flags, overlaps and gate status | ✅ Works |
| PR briefs, short or deep, with review threads and risk | ✅ Works |
| Dependabot verdicts with call-site analysis | ✅ Works |
| Issue triage grounded in code; duplicates, competing PRs, progress | ✅ Works |
| Stale and author-blocked PR decisions | ✅ Works |
| Labels, comments, change requests, approvals, branch updates (only on your request; drafted text shown first) | ✅ Works |
| GitHub Discussions: read and draft | ✅ Works |
| Long-term memory of incidents, decisions and preferences | ✅ Works |
| Maintainer dashboard tab | ✅ Works |
| Coding in the sandbox: clone, toolchains, reproduce, change, test (patch or push to your fork) | ✅ Works |
| Organisation pack: any repository, one directory of config | ✅ Works |
| Slack and scheduled digests | 🚧 Host install only, not in the sandbox |
| Merging | ⛔ Never, by design |

## What is verified, and the limits

**Verified in a real sandbox** (on the image as built): GitHub MCP with a real token (merge tools absent, a merge
refused by the proxy with `policy_denied`); REST and GraphQL mutations refused; the safety hooks; the memory plugin with
the network off; the dashboard; GitHub Copilot sign-in and a model reply; state kept across `--upgrade`. The
deterministic tools were also run against several repositories that are not Kyverno's, with only a pack.

**Not verified yet:** a full conversation on a non-Kyverno repository; a real reply from Anthropic, OpenAI or NVIDIA
keys (the request reaches them with the key swapped in); Slack and cron inside the sandbox.

**Limits to know about:**

- **Structure, not semantics.** It orders by files, branches and references. Two PRs on different files that depend on
  each other in code are not caught.
- **Review state is partial today.** It reads unresolved threads, bot approvals and checks, not human approvals or
  requested reviewers as structured data. Approval-aware ordering is on the roadmap.
- **One repository at a time.** Replies take on the order of half a minute; a ten-PR fetch takes a few seconds.
- **Your repository's conventions matter.** Without the shared CI labels or a pack it still runs, and tells you what
  it is missing.

## Roadmap

This phase is **data classification and visibility for maintainers.** What is built is above. What is planned, in order,
is not built yet:

| Next | Why |
|---|---|
| Approval-aware context: review decision, who approved, requested reviewers, head commit, computed owners | Answer "which merge will wipe approvals on the others?" from data |
| A fetch layer that retries and updates incrementally, plus a local git mirror for history, owners and conflicts | Faster and cheaper answers; code relations in milliseconds |
| A merge-order planner that minimises rebases and re-approvals, tests merges, and orders by risk | Fewer rebases when a batch lands |
| A snapshot the CI publishes, so every answer starts warm | Same answers in chat, in CI and on the dashboard |
| Contributor assistant for any organisation | Today it needs more per-organisation setup ([`docs/contributor.md`](docs/contributor.md)) |
| More shared CI actions | A CI-health report is in review; security triage and vulnerability scanning are sketched |

## Documentation

| For | Read |
|---|---|
| Writing your pack | [`docs/organisation-pack.md`](docs/organisation-pack.md) |
| Adopting the CI actions | [`docs/adopting-actions.md`](docs/adopting-actions.md), [`.github/actions/`](.github/actions/README.md) |
| Setup in detail, other platforms | [`docs/setup.md`](docs/setup.md), [`docs/getting-started.md`](docs/getting-started.md) |
| Daily use, data, dashboard, troubleshooting, removal | [`docs/usage.md`](docs/usage.md) |
| Security: what was tested, and the design | [`docs/container.md`](docs/container.md), [`docs/openshell-architecture.md`](docs/openshell-architecture.md), [`SECURITY.md`](SECURITY.md) |
| How it works and why | [`docs/how-it-works.md`](docs/how-it-works.md), [`docs/architecture.md`](docs/architecture.md) |
| Everything it can do | [`docs/capabilities.md`](docs/capabilities.md) |
| Running it on your host, not in a sandbox | [`docs/host-install.md`](docs/host-install.md) |
| The contributor assistant | [`docs/contributor.md`](docs/contributor.md) |
| Working on this repository | [`docs/development.md`](docs/development.md) |

Licensed under Apache-2.0. Not affiliated with GitHub or NVIDIA.
