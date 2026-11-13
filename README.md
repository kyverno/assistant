# AI Assistant for open-source maintainers and contributors

**Two AI assistants and a set of shared CI actions that help an open-source project cope with the flood of pull
requests and issues. The maintainer assistant works your queue and explains every call. The contributor assistant
helps people get a change merged. Neither can merge, and both run on your own machine inside an
[OpenShell](https://github.com/NVIDIA/OpenShell) sandbox that keeps your GitHub token out of the agent's reach.**

Built on [Hermes](https://hermes-agent.nousresearch.com). Kyverno is the first project to use it.

> 🚀 **You do not clone this repository.** Everything ships as a signed container image on GHCR, and one command
> downloads the launcher from that image and runs it:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh
> ```
>
> You need Docker, a GitHub account, and a GitHub Copilot subscription (or another model key). [Details below.](#get-started)

## Contents

[What this is](#what-this-is) · [Get started](#get-started) · [The maintainer assistant](#the-maintainer-assistant) ·
[The contributor assistant](#the-contributor-assistant) · [Shared CI actions](#shared-ci-actions) ·
[Prepare your repository](#prepare-your-repository) · [Built on Hermes](#built-on-hermes) ·
[Protected by OpenShell](#protected-by-openshell) · [Capabilities](#capabilities) ·
[What is verified, and the limits](#what-is-verified-and-the-limits) · [Roadmap](#roadmap) · [Documentation](#documentation)

## What this is

Three independent layers. Take one, or all of them:

| Layer | What it is | Needs a model? | Who adopts it |
|---|---|---|---|
| **Shared CI actions** (`.github/actions/`) | GitHub Actions that label PRs from the paths they change, mark each PR `ready-for-review` or `needs-author-action` from its checks and review threads, warn and then close PRs and issues an author has left quiet | No | A repository, from a workflow file |
| **Maintainer assistant** | An on-demand agent that builds your review queue, orders it, briefs you on any PR, triages issues and Discussions, and does the follow-up paperwork | Yes | One maintainer, on their own machine |
| **Contributor assistant** | An on-demand coach that finds work worth doing, explains how the code fits together, helps prepare an evidence-backed PR and explains review feedback | Yes | One contributor |

The layers share data: the CI actions label things in a fixed way, and the assistants read those labels instead of
recomputing them. A repository that runs the actions gives the maintainer assistant its signals for free.

```
 your machine
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │  you ──► the launcher ──► OpenShell gateway   (holds your GitHub token and  │
 │          (from the image)  any model API key, outside the sandbox; an       │
 │                            allowlist proxy decides what it may reach)       │
 │                                     │                                       │
 │            ┌────────────────────────▼───────────────────────────────┐       │
 │            │ SANDBOX: Hermes + a profile (maintainer or contributor)│       │
 │            │ skills · plugins · memory · a terminal for coding      │       │
 │            └────────────────────────────────────────────────────────┘       │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        ▼
                  GitHub (read APIs, the GitHub MCP server) · GitHub Copilot or your model
```

## Get started

### What you need

| | |
|---|---|
| **Docker** | Docker Desktop 4.60 or newer on a Mac (running, with at least 4 GB of memory), or Docker Engine on Linux. About 20 GB of free disk |
| **A GitHub account** | The repository you want to work on must be readable by it |
| **A model** | A **GitHub Copilot** subscription (the default), or an Anthropic, OpenAI or NVIDIA API key. See [why Copilot](#why-copilot) |
| **A platform** | Verified on Apple Silicon Macs, and on Linux amd64 on a CI runner. Linux needs kernel 5.13 or newer (for Landlock). Other combinations are untested |

You do **not** install Hermes, Python, or OpenShell, and you do not clone anything. The launcher installs OpenShell if it
is missing.

### Run it

```bash
curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh
```

The script pulls the signed image from `ghcr.io/kyverno/assistant`, copies the launcher out of it (so the launcher and the
image always come from the same release), runs it, and removes the copy. To make it a command you can type, add this to
your shell profile:

```bash
alias assistant='curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh -s --'
```

Then `assistant`, `assistant --profile contributor`, `assistant --upgrade` and so on. In the rest of the docs,
`assistant` means this.

### What the installer does, and what you do

| Step | What happens | You |
|---|---|---|
| 1. Docker and OpenShell | Checks Docker, installs OpenShell and its gateway if missing | Nothing, unless Docker is not running |
| 2. Image | Pulls the image the first time (about 1.2 GB compressed) | Wait a few minutes |
| 3. GitHub | Asks which assistant (**maintainer** or **contributor**), then the repository as `owner/repo` (there is no default), then how to authorize GitHub. It checks the token against the repository, then stores it **in OpenShell, not in the sandbox** | Type the repository, then sign in |
| 4. Sandbox | Creates the sandbox and applies the network policy | Wait a minute or two |
| 5. Copilot | Reads your GitHub username from the token, then starts the GitHub Copilot sign-in: it shows a **code and a page** | Open <https://github.com/login/device>, enter the code, approve it, then pick a model |

The chat opens after about 30 seconds. Only the first run asks questions: after that, `assistant` takes you straight back
in, and it keeps your memory, chat history, sign-in and model across upgrades.

### The two sign-ins

**1. GitHub.** You are offered two ways, and either one ends up stored in OpenShell:

- **Sign in with GitHub (`gh`)**: a browser sign-in, nothing to create. Note that `gh` always asks for broad scopes, so
  the token can act on every repository you can, private ones included. The assistant still cannot merge.
- **Paste a fine-grained token** scoped to the **one repository**: the tighter option. The permissions it needs are
  [below](#4-create-a-token) (maintainer) and in [`docs/getting-started.md`](docs/getting-started.md#tokens) (contributor).

**2. GitHub Copilot (the model).** The launcher starts the same device-code login Copilot's own tools use: it prints a code,
you approve it at github.com/login/device, and Hermes keeps the resulting token inside the sandbox's Hermes home. It is
the model the assistant thinks with. To use a key you already have instead, start with `assistant --provider anthropic`
(or `openai`, `nvidia`): it asks for the key once and keeps it in OpenShell, like the GitHub token.

#### Why Copilot

The people this is built for already have it. CNCF gives **GitHub Copilot Enterprise to official CNCF project
maintainers** (they request it through the CNCF Service Desk; announced in
[December 2025](https://contribute.cncf.io/blog/2025/12/16/github-copilot-enterprise-for-maintainers/)), and GitHub gives
**Copilot Pro free to maintainers of popular open-source projects**
([eligibility](https://docs.github.com/en/copilot/how-tos/manage-your-account/get-free-access-to-copilot-pro)). So there is
no API key to buy, paste or leak: sign in once and go. Which models you can pick depends on your plan.

All the options, per platform, plus upgrading, removing, tokens and troubleshooting:
[`docs/getting-started.md`](docs/getting-started.md).

## The maintainer assistant

It works your pull-request queue and issue backlog the way a careful co-maintainer would: it finds what is ready, works
out the order, explains every call with a link, and does the paperwork.

| The problem | What it does |
|---|---|
| "Which PRs can I merge today?" | Fetches the whole candidate set, drops what is not ready, and gives an order with a reason per PR |
| Merging one PR forces rebases on others | Flags PRs that touch files another PR changes, so you see the rebase coming |
| PRs that depend on each other | Finds stacked branches, "depends on #N" references and generated-file input/output pairs, and orders them |
| A red branch gate blocks everything | States it as a fact on every PR it affects, and leaves the bypass decision to you |
| Dependabot PRs pile up | A verdict per PR (merge now, fix first, your review, wait, close), from who calls the dependency |
| Issues need triage | Classifies from the real code, finds duplicates and competing PRs, says how far along each fix is |
| Stale and abandoned PRs | Recommends close, reassign, or take over the branch, with the reason from the linked issue |
| GitHub Discussions | Reads the thread and drafts a reply; nothing is posted until you say so |
| "What did we decide last time?" | Remembers past incidents, rejected PRs and your working style between sessions |

Everything it says is linked to the PR, issue, file or run it comes from. When it looked and found nothing, it says "not
found there" instead of guessing.

### How it thinks

**Code computes the facts, the model makes the judgment, and you make the decision.** Anything that can be computed exactly
(a dependency, a file overlap, a gate) is computed by a deterministic tool, so the model does not guess it. The model then
explains and ranks, citing what the tools returned.

| Signal | Where it comes from | What it tells you |
|---|---|---|
| Stacked branches | A PR's base branch is another candidate's head | Merge the base first |
| Generated-file order | One PR changes a generated file's *input*, another its *output* | The input has to land first, or the output goes stale |
| Explicit references | "Depends on #N", "Blocked by #N", "Stacks on #N", "Parent: #N" in the body | Order, including PRs outside the set (it fetches their state) |
| Competing PRs | Two PRs close the same issue | Duplicate effort: flagged for you, not ordered |
| Same-file overlap | Two PRs touch one file | A likely rebase conflict; it can compare the real diff hunks to see if the *lines* collide |
| Same-package overlap | Same directory, different files | Cannot conflict in git, but worth reviewing together |
| Rebase flag | A PR touches files an earlier-ranked PR changes | It will need updating after that one merges |
| Branch gate | An open gate-failure issue for the PR's base branch | Merging is blocked on that branch whatever the files; review is not |
| File class | Your organisation pack: generated, API surface, critical, test-only | How risky the change is after merge |
| Past incidents | Its memory of package pairs that broke the post-merge suite before | A specific, citable risk |

Cycles are detected and handed to you by number. Among PRs with no hard dependency between them it ranks with a fixed
ladder and says which rung decided each position: related PRs stay together; **severity of the closing issue**, judged
from its content (critical: crash, outage, data loss, security; then high, medium, low); release-urgency labels and
milestone proximity; bot approval and unresolved threads; the author's relationship to the project; size as a pure
tiebreak. Anything you said this session overrides the ladder. **Readiness is separate from priority:** a high-severity PR
with changes requested or a failing check is never listed under "approve now"; the blocker is named instead.

An illustrative queue answer (numbers invented):

```
Queue for milestone 1.20: 14 of 31 open PRs

Group 1: no hard dependency between these
  #18102  split admission handlers      closes #17990 (CRITICAL: crash on startup)
          ✓ checks green · bot-approved · 0 open threads
          ⚠ #18110 is stacked on this one: merge #18102 first
  #18077  fix cleanup TTL parsing       closes #17811 (HIGH: regression in 1.19)
          ✓ ready · touches pkg/cleanup, also changed by #18090 → review together
Group 2
  #18110  stacked on #18102             ⏳ waits for #18102
  #18090  rename policy fields          ⚠ rebase flag: shares api/v1/policy_types.go with #18102
Gate: main is red (issue #18121). Every PR above is blocked on merge until it clears.
```

### Talking to it

| Ask | You get |
|---|---|
| "What's my review queue for 1.20?" | Asks for a focus if you gave none, then the grouped, ordered queue |
| "Brief me on #1234" | Short by default; deeper on request: threads, risk, suggested action |
| "Which Dependabot PRs can I merge today?" | A verdict per PR with call sites, release notes and review findings |
| "Triage the 10 oldest issues with no PR" | Class, severity, duplicates, competing PRs and progress, grounded in the code |
| "Which stale PRs should I close?" | Close, reassign or take over, each with the issue state behind it |
| "Label #1234 area/cli and ask the author to fix DCO" | Applies the label you asked for, and shows you the comment before posting it |

Results also land on a read-only **Maintainer tab** at `http://127.0.0.1:9219/maintainer?profile=maintainer` (the launcher
prints it). What you ask for explicitly (a label, an approval) it does and reports; anything with words in it (a comment, a
review, a nudge, a Discussion reply) it drafts and shows you first. **It never merges and never applies a gate-bypass
label.** It orders by what it can prove (files, branches, references), not by whether one change semantically breaks
another, and it says so when asked.

## The contributor assistant

A coach and a careful pair of hands for one person contributing to a repository. They do the work in the session: choosing
what to work on, understanding the code, changing it, getting it reviewed, and growing from a first PR toward reviewer and
maintainer. **They stay the author:** they decide, they send, and they answer for it.

| Skill | What it does |
|---|---|
| `find-work` | Matches issues to your areas and shows each one's full history *before* recommending it (claimed? an open PR? probably fixed?), and how to join work in progress. Says plainly when an issue is a poor choice |
| `understand-code` | Explains how a file or folder fits in: governing docs, imports and importers, tests, owners, history, precedent PRs, then the code itself. It finishes by asking you to explain it back |
| `make-the-change` | Branch, reproduce, change, test and prove it in the sandbox, with signed-off commits and an evidence ledger. It checks the change against the project's own review rules, then drafts the PR from the template |
| `review-loop` | After the PR is open: explains every review comment and failing check, says which look right and which deserve pushback with evidence, and drafts reviews of other people's PRs for you to post |
| `grow` | The path from first PR to reviewer: what you have done, what the project's ladder asks for, what to work on next |

Its tools compute the facts it cites: what the repository says about a path (docs, labels, owners), how a path fits in, how
the area was changed before and what the maintainers decided and why, what happened to an issue, where your PR stands, and
what you have done so far.

**What it will not do for you:** the AI-assistance line in a PR template and any reply to a reviewer are yours to write (it
drafts nothing for those); it will not claim a test passed that it did not run; and before a PR is drafted it asks you to
explain the cause and the fix in your own words. It can write two things to GitHub, a comment and a PR from your own fork,
and only after you have seen the exact text and said yes. **It cannot merge, approve or submit a formal review**, and hooks
block those even if a tool appeared. Pushing is off unless you start it with `--push-to-fork`, and then only to your own
fork.

**Status:** it works, and it is configured for Kyverno today: its pack carries Kyverno's contribution ladder, and another
project needs more setup for it than for the maintainer assistant ([`docs/contributor.md`](docs/contributor.md)). Background
updates (new reviews, failing checks) need the messaging gateway, which the sandbox does not run yet.

## Shared CI actions

Deterministic composite GitHub Actions that need **no model, no container and no secret of yours**: they run on GitHub with
the workflow's own token, in **preview** (they print what they would do and change nothing) until a repository admin turns
them on.

| Action | What it does |
|---|---|
| [`pr-management`](.github/actions/pr-management/README.md) | Labels PRs from the paths they change; marks each `ready-for-review` or `needs-author-action` from its checks and review threads (DCO and CI built in); flags `workflow-approval-required` and `merge-conflicts`; warns, then closes, PRs an author has left quiet |
| [`issue-management`](.github/actions/issue-management/README.md) | Labels issues a merged PR may have resolved, warns then closes issues waiting on the reporter, flags idle untriaged issues. Only the reporter rule ever closes anything |
| `ci-health` (in review) | A read-only report on one workflow's recent runs: shared root causes, flaky and persistent jobs, the commits since the last green run |

You reference an action from a short workflow (`uses: kyverno/assistant/.github/actions/pr-management@<full commit SHA>`), so
the actions themselves need no checkout. `scripts/adopt-ci.sh` is a helper that opens the adoption PR in each repository you
name, and it **does run from a checkout of this repository** (it reads the templates next to it); you can also copy the files
from [`adopt-ci-templates/`](adopt-ci-templates/) by hand. Steps, files and permissions:
[`docs/adopting-actions.md`](docs/adopting-actions.md).

## Prepare your repository

The maintainer assistant is only as sharp as what your repository tells it. Three things make it effective, and the first
comes **before** you install anything. (The contributor assistant reads the same files.)

### 1. Adopt the shared CI and labels first

The queue and stale-PR skills read the labels the actions maintain: `ready-for-review`, `needs-author-action`,
`workflow-approval-required`, `stale`, `merge-conflicts`, and path labels from your `.github/labels.yml`. A repository that
already runs them hands the assistant its signals for free. Without them it still runs, but it has no readiness labels to go
on and says so. See [Shared CI actions](#shared-ci-actions) above.

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

Without a pack the assistant still works: it reads every file as ordinary, mentions no gate, and tells you which signals it
is missing. A mistake in the pack is an error that names the key. Reference: [`docs/organisation-pack.md`](docs/organisation-pack.md);
Kyverno's is in [`container/org/kyverno/`](container/org/kyverno/).

> ⚠️ **Today the published image carries Kyverno's pack.** For your own pack you build your own image from a checkout of this
> repository (`container/assistant --build --org your-org`), which is the one case that needs a clone. Reading the pack from
> the adopting repository itself, so that the published image works for everyone, is the planned fix
> ([roadmap](#roadmap)).

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

A fine-grained personal access token for the **one repository**, from your own account: Contents **Read**, Pull requests
**Read and write**, Issues **Read and write**, Checks **Read**, and Code scanning, Dependabot and Secret scanning alerts
**Read**; organisation Members **Read** if CODEOWNERS uses teams. No Contents write and no Administration: GitHub itself then
refuses to merge. Or let the launcher use your `gh` sign-in. Step by step: [`docs/setup.md`](docs/setup.md#1-create-a-github-token).

## Built on Hermes

[Hermes](https://hermes-agent.nousresearch.com) (from Nous Research) is the open-source agent runtime. This project is two
**Hermes profile distributions**, `maintainer` and `contributor`. A profile is a self-contained agent:

| Piece | What it is here |
|---|---|
| `SOUL.md` | The principles: what the agent may do, what it must never do, how it reasons |
| **Skills** | Step-by-step procedures: `pr-queue`, `issue-triage`, `dependabot-queue`, `pr-actions`, `discussions`, `find-work`, `make-the-change`, ... |
| **Plugins** | The deterministic tools, in plain Python: `repo-intel` (fetch, sequencing, the pack) and the dashboard for the maintainer; the contributor's knowledge, history and journey tools |
| **Hooks** | Safety checks that run before a tool does: block merge-like tools, guard `git push`, keep the terminal inside the sandbox |
| **Memory** | A long-term memory plugin, so the assistant recalls incidents, decisions and preferences between sessions |
| **Config** | Which tools each profile may see; for example the contributor's GitHub tool list has no review or merge tool |

Plugins hold the facts, skills hold the method, the model holds the judgment. See [`docs/how-it-works.md`](docs/how-it-works.md)
and [`docs/architecture.md`](docs/architecture.md).

## Protected by OpenShell

An assistant that reads strangers' issues and PRs will, sooner or later, read text written to manipulate it. The design
answer is that **a tricked agent must still be unable to do harm.** [OpenShell](https://github.com/NVIDIA/OpenShell) is the
reason that holds: the strongest limits sit **outside the sandbox, where the agent cannot edit them.**

| Protection | How it works |
|---|---|
| **Your GitHub token never enters the sandbox** | The agent sees a meaningless placeholder. The gateway's proxy swaps in the real token only on requests to the listed GitHub endpoints, and only from the listed programs. A prompt-injected agent told to print its credentials prints the placeholder |
| **GitHub is read-only at the proxy** | On `api.github.com` REST is read-only and GraphQL mutations (including `mergePullRequest`) are refused by the proxy, not by a rule the agent could edit |
| **Every GitHub tool call is checked by name** | On the GitHub MCP server the proxy allows only the tools on the allowlist and refuses everything else, plus anything named like merge, delete or force-push, even when hidden in a batched request |
| **Deny-by-default network** | Anything not listed is blocked, per host, path, method and program: `git` and `curl` get read-only GitHub, the toolchain hosts are GET-only for the installers, only Hermes' own Python may use the Copilot login |
| **Read-only code and hooks** | Landlock (a hard requirement) makes the system, Hermes' code, the baked safety hooks and the organisation pack read-only. The agent can write only `/sandbox`, `/tmp` and Hermes' home |
| **A full audit trail** | `openshell logs assistant-maint` (or `assistant-contrib`) shows every request the sandbox made and what was allowed or denied |
| **Asking, not widening** | If a task needs a blocked host, the agent can propose the narrowest rule. You approve it on your own computer; it cannot approve it itself |
| **Pushes only if you opt in** | `--push-to-fork` adds a separate credential for your own fork. A hook still refuses force pushes, deletes and pushes to main or release branches, and the network rule refuses every other repository |

**If the agent is tricked:** told to print or leak your token, it holds only a placeholder. Told to merge, there is no merge
tool, the proxy refuses merge tool names and GraphQL mutations, and the token has no merge permission. Told to send data to a
host it chose, the request is blocked and logged. Told to reach GitHub with the token through another program, the token is
only swapped in for the listed ones. Told to rewrite its own safety hooks, they are read-only to it.

**Honest limits.** Layers that live inside the sandbox (the tool list in `config.yaml`, the hook that blocks merge-like names)
can be edited by a manipulated agent, because it has a terminal and file tools. The ones that matter (the token, the proxy,
the network allowlist, the read-only files) do not depend on anything it can write. It can still *say* something misleading,
so check the links it cites before you act. The one credential inside the sandbox is the Copilot sign-in token, which Hermes
needs to call the model; the network allowlist is what stops it going anywhere else. What was tested, and how: [`docs/container.md`](docs/container.md); the design:
[`docs/openshell-architecture.md`](docs/openshell-architecture.md), [`docs/openshell.md`](docs/openshell.md) and
[`SECURITY.md`](SECURITY.md). Without OpenShell (`assistant --no-openshell`, for an Intel Mac or Docker only) the protection is
much weaker and the docs say exactly how.

## Capabilities

| | Maintainer | Contributor | CI actions |
|---|---|---|---|
| Ordered merge queue with reasons, rebase flags, overlaps, gate status | ✅ | | |
| PR briefs, with review threads and risk | ✅ | ✅ (your own PR's state) | |
| Dependabot verdicts with call-site analysis | ✅ | | |
| Issue triage grounded in code; duplicates, competing PRs | ✅ | ✅ (issue history and claims) | ✅ (possibly-resolved, waiting-on-reporter) |
| Finding work worth doing, matched to your areas | | ✅ | |
| Explaining code: docs, importers, tests, owners, precedent PRs | | ✅ | |
| Stale and author-blocked PR decisions | ✅ | | ✅ (warn, then close) |
| Readiness and path labels | reads them | reads them | ✅ |
| Labels, comments, change requests, approvals, branch updates | ✅ on your request | comments and a PR from your fork, text shown first | |
| Discussions: read and draft | ✅ | | |
| Review feedback explained; reviews of others' PRs drafted | | ✅ | |
| Journey and ladder progress | | ✅ | |
| Long-term memory | ✅ | ✅ | |
| Dashboard tab | ✅ | | |
| Coding in the sandbox: clone, toolchains, reproduce, change, test | ✅ | ✅ | |
| Merging | ⛔ never | ⛔ never | ⛔ never |

Everything each assistant can do, in detail: [`docs/capabilities.md`](docs/capabilities.md). Slack and scheduled digests work in
the [host install](docs/host-install.md) only, not in the sandbox.

## What is verified, and the limits

**Verified in a real sandbox** (on the image as built): GitHub MCP with a real token (merge tools absent; a merge refused by
the proxy with `policy_denied`); REST and GraphQL mutations refused; the safety hooks; the memory plugin with the network off;
the dashboard; GitHub Copilot sign-in and a model reply; state kept across `--upgrade`. The deterministic maintainer tools were
also run against several repositories that are not Kyverno's, with only a pack.

**Not verified yet:** a full conversation on a non-Kyverno repository; a real reply from Anthropic, OpenAI or NVIDIA keys (the
request reaches them with the key swapped in); Slack and cron inside the sandbox; the contributor assistant on a project
other than Kyverno.

**Limits to know about:**

- **Structure, not semantics.** The maintainer assistant orders by files, branches and references. Two PRs on different files
  that depend on each other in code are not caught.
- **Review state is partial today.** It reads unresolved threads, bot approvals and checks, not human approvals or requested
  reviewers as structured data. Approval-aware ordering is on the roadmap.
- **One repository at a time**, and replies take on the order of half a minute.
- **Your repository's conventions matter.** Without the shared CI labels or a pack it still runs, and tells you what it is missing.

## Roadmap

The current phase is **data classification and visibility for maintainers**: knowing what is in the queue, how it relates, and
what to do first. What is built is above. What is planned, in order, is not built yet:

| Next | Why |
|---|---|
| The organisation pack read from your own repository (for example a file under `.github/`) | So the published image works for any project with no custom build and no clone |
| Approval-aware context: review decision, who approved, requested reviewers, head commit, computed owners | Answer "which merge will wipe approvals on the others?" from data |
| A fetch layer that retries and updates incrementally, plus a local git mirror for history, owners and conflicts | Faster, cheaper answers; code relations in milliseconds |
| A merge-order planner that minimises rebases and re-approvals, tests merges, and orders by risk | Fewer rebases when a batch lands |
| A snapshot the CI publishes, so every answer starts warm | The same answers in chat, in CI and on the dashboard |
| The contributor assistant for any project (without a required ladder) | Today it is configured for Kyverno |
| More shared CI actions: security triage, vulnerability scanning | Same preview-first design |

## Documentation

| For | Read |
|---|---|
| Installing, per platform; sign-ins, tokens, models, upgrading, removing, troubleshooting | [`docs/getting-started.md`](docs/getting-started.md) |
| Daily use, data, the dashboard, coding in the sandbox | [`docs/usage.md`](docs/usage.md), [`docs/setup.md`](docs/setup.md) |
| Adopting the CI actions | [`docs/adopting-actions.md`](docs/adopting-actions.md), [`.github/actions/`](.github/actions/README.md) |
| Writing your organisation pack | [`docs/organisation-pack.md`](docs/organisation-pack.md) |
| Security: what was tested, and the design | [`docs/container.md`](docs/container.md), [`docs/openshell-architecture.md`](docs/openshell-architecture.md), [`SECURITY.md`](SECURITY.md) |
| How it works and why | [`docs/how-it-works.md`](docs/how-it-works.md), [`docs/architecture.md`](docs/architecture.md) |
| Everything each assistant can do | [`docs/capabilities.md`](docs/capabilities.md) |
| The contributor assistant | [`docs/contributor.md`](docs/contributor.md) |
| Running on your host instead of a sandbox | [`docs/host-install.md`](docs/host-install.md) |
| Working on this repository | [`docs/development.md`](docs/development.md) |

Licensed under Apache-2.0. Not affiliated with GitHub, NVIDIA or CNCF.
