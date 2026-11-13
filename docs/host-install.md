# Running the assistant directly on your machine (without the sandbox)

> **The recommended way to run the assistant is now inside an OpenShell sandbox:** see the
> [README](../README.md) and [`container.md`](container.md). This page is the older route, where Hermes is installed
> on your own machine and `scripts/install.sh` sets up a profile. It still works, it supports Slack and scheduled jobs
> (which the sandbox does not yet), and it needs more of your machine set up correctly: the Hermes CLI, Python with
> PyYAML, no stray `GITHUB_TOKEN` in your shell, and so on. The agent also runs with access to your own files and network, which
> the sandbox does not allow.

## Quick start

```bash
git clone https://github.com/kyverno/assistant.git
cd assistant
./scripts/install.sh      # installs, prompts for your credentials, then verifies everything
maintainer chat
```

You need Docker, the Hermes CLI and a GitHub token. The model is chosen in Hermes, as described in step 5.

## Setup

### 1. Prerequisites

| Requirement | Notes |
|---|---|
| [Docker](https://docs.docker.com/get-docker/) | Must be running. The Slack tool runs as a container, and the optional sandboxed terminal uses Docker. |
| [Hermes CLI](https://hermes-agent.nousresearch.com) | On your `PATH`. |

### 2. GitHub token

Create a fine-grained personal access token scoped to the repo you will point the assistant at
(for example `kyverno/kyverno`):

| Permission | Access |
|---|---|
| Contents | Read |
| Pull requests | Read & Write |
| Issues | Read & Write |
| Checks | Read |
| Code scanning alerts | Read |
| Dependabot alerts | Read |
| Secret scanning alerts | Read |
| Organization members | Read |

Optional, for the extra tools: Organization projects (Read & Write) for project boards,
Administration (Read) for repository rulesets, Organization custom properties (Read). Tools whose
permission is missing return an error and the rest keep working.

Do not grant Contents Write, merge, or admin scopes. See [`docs/architecture.md`](architecture.md)
for the reasoning.

### 3. Slack (optional, skip for CLI-only use)

One Slack app serves two purposes: the **bot** you `@mention` or DM to talk to the assistant, and a
**tool** the assistant uses to read the maintainers channel and post into it (for example the merge
sequence, or a reply in a PTAL thread). The app, the channel and the people using it must all be in
the **same Slack workspace**.

<details>
<summary><b>Create the Slack app</b></summary>

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
   Name it and pick your workspace.
2. **OAuth & Permissions** → add these Bot Token Scopes:
   `chat:write`, `channels:history`, `channels:read`, `app_mentions:read`, `groups:read`,
   `mpim:read`, `im:read`.
   The last three are needed even though the assistant only reads one public channel: the Slack
   tool fetches every channel type at startup and needs a scope for each.
3. **Socket Mode** → turn it on → generate an App-Level Token with the `connections:write` scope.
   Save it.
4. **Event Subscriptions** → turn it on → subscribe to the bot events `app_mention` and
   `message.channels`.
5. **Install App** to your workspace → copy the **Bot User OAuth Token**.
6. Invite the bot to your maintainers channel (`/invite @<bot-name>`). Note the channel ID and your
   own Slack member ID (both are in the channel-details and profile menus).
7. If you change scopes after installing, click **reinstall your app** on the OAuth & Permissions
   page so they take effect.

</details>

### 4. Install

```bash
./scripts/install.sh
```

The script is idempotent: re-run it as often as you like and it only does what is left.

1. **Install and prompts:** installs the profile, then asks for each missing credential (GitHub
   token, username, repo) and whether to set up Slack and the sandboxed terminal. Values are saved to
   `~/.hermes/profiles/maintainer/.env`. Press Enter at any prompt to skip it.
2. **Organisation pack:** copies your pack from `container/org/` into the profile (see
   [`organisation-pack.md`](organisation-pack.md)): `ASSISTANT_ORG=<name>` if you set it, otherwise the one named after the owner
   of your repository. Without one the assistant still runs, but knows no file classes or merge gate, and the installer says so.
3. **Checks:** installs the messaging gateway (only if Slack is configured), checks that the GitHub
   and Slack tools are reachable and the hooks are approved, and prints ✓ or ✗ for each check.

If you skipped a prompt, the script stops and lists what is missing. Fill it in at the `.env`
(created from the profile's `.env.EXAMPLE`) and re-run. When the script isn't run in a terminal, it
skips the prompts and works this way from the start.

| Variable | Required | Purpose |
|---|---|---|
| `REPO_GITHUB_TOKEN` | yes | Token from step 2 |
| `MAINTAINER_GITHUB_LOGIN` | yes | Your GitHub username |
| `ASSISTANT_REPO` | yes | `owner/repo` the assistant manages (no default: the installer asks) |
| `ANTHROPIC_API_KEY`, `COPILOT_GITHUB_TOKEN`, … | no | Optional model keys; Hermes can also sign in to a provider for you (step 5) |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack only | Bot and app-level tokens |
| `SLACK_ALLOWED_USERS` | Slack only | Your Slack member ID |
| `SLACK_HOME_CHANNEL` | Slack only | Maintainers channel ID |

[`docs/deployment.md`](deployment.md) describes each variable in full. Pasting real values into
`.env` once is the one manual step: `hermes profile install` has no non-interactive way to accept
credentials.

### 5. Run it

```bash
maintainer chat
```

Or mention the bot in the Slack channel you invited it to.

The model is yours to choose, the way Hermes does it: run `hermes -p maintainer model` and pick any
provider, type `/model` in a chat, or add a provider key such as `ANTHROPIC_API_KEY` to the profile's
`.env`. A Claude Pro or Max login works only with purchased extra usage
([Anthropic's rules](https://code.claude.com/docs/en/legal-and-compliance),
[Hermes' notes](https://hermes-agent.nousresearch.com/docs/integrations/providers)).

Your model choice and `.env` survive re-running `./scripts/install.sh` and `./scripts/update.sh`.

### 6. Sandboxed terminal (optional)

The installer asks whether to set up a terminal that runs inside an OpenShell sandbox, with read-only
GitHub access, no tokens, and an audit log of every allowed and denied request. Accept it to let the
assistant reproduce issues and run commands in the same chat; when something is blocked it can ask
for narrow access, which only you approve. Details, the policy and the audit commands are in [docs/openshell.md](openshell.md).

### 7. Turn on the review digest (optional)

The profile ships a weekday-morning merge-sequence digest that posts to your Slack channel. It
arrives **paused**, so nothing posts to Slack on a schedule until you resume it:

```bash
hermes cron list                          # shows "review-digest", paused
hermes cron resume review-digest
hermes cron edit review-digest    # change the time or channel
```

### 8. Update

```bash
./scripts/update.sh
```

Pulls the latest version of this repo and re-runs the installer: the profile (config, skills,
hooks), the sandbox policy and image, and the checks are refreshed. Your `.env`, model choice and
sandboxed terminal are kept.

## The dashboard

A browser board that shows what the agent just told you, linked, grouped and sortable, so you don't
scroll the chat or copy links by hand. (The sandbox runs it too; see the [README](usage.md#the-dashboard).)

```bash
hermes -p maintainer dashboard
```

Pick **Maintainer** in the sidebar under Plugins, or open
`http://127.0.0.1:9119/maintainer?profile=maintainer`. It refreshes every 15 seconds. Keep it open next to
the chat: it fills when the agent answers a queue, stale-PR, issue-triage or Discussions request,
not in the background. The tabs are described in the [README](usage.md#the-dashboard). The Maintainer tab is
read-only; confirming and acting still happen in the chat.
