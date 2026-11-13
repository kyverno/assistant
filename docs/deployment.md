# Deployment

> **Running in the OpenShell sandbox?** That is now the recommended way, and it has its own instructions: see the [README](../README.md) and [`container.md`](container.md). This runbook covers the host install.

The assistant is a Hermes profile distribution — there's no server to stand
up, no webhook receiver, no GitHub App to register. Install flow only.

## Install

```bash
hermes profile install github.com/kyverno/assistant --name maintainer --alias -y
```

`--alias` takes no value — it creates a shell wrapper command named after
`--name` (or the manifest's own name if `--name` is omitted). `--alias
maintainer` is a common-looking but wrong invocation; it parses `kyverno` as a
second, unexpected positional argument and fails with "unrecognized
arguments."

(While this is still prototyped in this sandbox repo rather than published
separately, install from a local checkout instead: `hermes profile install .
--name kyverno --alias -y`.)

`./scripts/install.sh` runs this plus everything else below (gateway,
sanity checks) and is the recommended path — see the README's Setup
section. What follows here is what it's actually doing, for reference or
manual use.

`hermes profile install` does **not** prompt for or write any env vars —
it just installs the profile and tells you to copy `.env.EXAMPLE` to `.env`
(`~/.hermes/profiles/maintainer/.env` — separate from this repo, never
committed) and fill in these yourself:

- `REPO_GITHUB_TOKEN` — fine-grained PAT: Contents Read, Pull requests Read & Write,
  Issues Read & Write, Checks Read, Code scanning alerts Read, Dependabot
  alerts Read, Secret scanning alerts Read, org Members Read (the last one
  for CODEOWNERS team resolution). Optional: org Projects Read & Write,
  Administration Read (rulesets), org Custom properties Read. Do **not** grant Contents Write or any
  merge/admin scope — the toolset doesn't call a merge endpoint, but the
  token shouldn't be able to either (belt and suspenders, see
  `docs/architecture.md`).
- `MAINTAINER_GITHUB_LOGIN` — the installing maintainer's GitHub username.
- `SLACK_BOT_TOKEN` (`xoxb-*`) / `SLACK_APP_TOKEN` (`xapp-*`, Socket Mode) — the
  Slack app's bot and app-level tokens. **Two different things use these** (see
  "Two separate Slack credential consumers" in `docs/architecture.md`):
  Hermes' own chat interface needs both, plus Socket Mode enabled and
  `app_mentions:read` + `message.channels` event subscriptions; the `slack`
  MCP server (reading/posting `SLACK_HOME_CHANNEL`) only ever uses
  `SLACK_BOT_TOKEN`, never `SLACK_APP_TOKEN`. Scopes needed either way:
  `chat:write`, `channels:history`, `channels:read`, `app_mentions:read`,
  plus `groups:read`, `mpim:read`, `im:read` — the `slack` MCP server fetches
  all four Slack channel types in one call at boot regardless of what this
  profile uses, and fatally exits if any one scope is missing, even though
  this profile only ever reads one public channel. Reinstall the app after
  subscribing to events or changing scopes. The `slack` MCP server also
  validates its token at process startup and exits immediately on an
  invalid one — a wrong/expired `SLACK_BOT_TOKEN` means that container
  never starts, not that it starts with reduced capability.
- `SLACK_ALLOWED_USERS` — the installing maintainer's Slack member ID (keeps
  this instance answering only them).
- `SLACK_HOME_CHANNEL` — the maintainers channel to read priority signals from
  and post the queue in. Invite the bot to it (`/invite @<your bot>`).
- `ASSISTANT_REPO` — `owner/repo` this instance manages. Required: there is no default.
- `ANTHROPIC_API_KEY`, `COPILOT_GITHUB_TOKEN` or another provider key — optional.
  The model is chosen in Hermes (`hermes -p maintainer model`, or `/model` in a
  chat), and the installer never changes it. `COPILOT_GITHUB_TOKEN` is separate
  from `REPO_GITHUB_TOKEN`, which stays the repo-scoped PAT for the GitHub tools.

## Run

```bash
kyverno chat
```

Talking to it on Slack needs one more thing `kyverno chat` doesn't cover:
Hermes runs exactly one gateway *per host* (not per profile) as the inbound
process for every profile's messaging platforms — `kyverno chat` only starts
a foreground CLI session, it doesn't make the gateway listen for Slack
mentions. `scripts/install.sh` does this automatically when Slack
credentials are present in `.env`; by hand, install the host gateway once,
from the `default` profile (it serves every profile, including this one):

```bash
hermes profile use default
hermes gateway install
hermes gateway status   # confirm it's supervised and running
```

`~/.hermes/logs/gateway.log` should show `slack connected (profile:
kyverno)`. A per-profile `hermes gateway run`/`install` fails on purpose
(`exited with code 78`) once a host gateway exists — that's the double-bind
guard (two pollers on one bot token, port conflicts), not a bug.

## Update

```bash
hermes profile update kyverno
```

Pulls the latest distribution version; the maintainer's own `.env`, memories,
and sessions are untouched.

## Validation plan

Before anything past v1 (see `docs/architecture.md`), validate end-to-end with
2-3 real maintainers on real PR queues — install, point at their actual repo,
confirm the queue reasoning (stacked PRs, generated-file conflicts, post-merge
CI risk) matches what they'd conclude by hand. Only after that is v2 (merge,
gated) worth building.

`skills/` is written (four skills — see README). `scripts/install.sh`
already runs the checks below; by hand, confirm the toolset matches
`config.yaml`:

```bash
hermes mcp list
hermes mcp test github
hermes mcp test slack
```

The hosted GitHub server needs a token to answer at all (`401` without one), so there is no
credential-free probe. `hermes mcp test github` lists the tools it actually offers; that list must
equal `mcp_servers.github.tools.include` and must not contain a merge tool.

The Slack side can't be checked credential-free the same way — the server
exits before the MCP handshake on an invalid or missing token (see above) —
but a real token, once obtained, is enough to run `hermes mcp test slack`
directly; no separate manual container probe is needed the way it once was.
