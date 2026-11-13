# Using it day to day

Running, upgrading and removing the assistant, where its data lives, the dashboard and the coding environment.

In the commands below, `assistant` is the installer command from the [README](../README.md#run-it) (an alias for the one-line
install; `container/assistant` if you work from a clone, which you do not need to).

## Everyday use

| To | Run |
|---|---|
| Start, or come back later | `assistant` |
| Open a second chat at the same time | `assistant` in another terminal |
| Open the dashboard | http://127.0.0.1:9219/maintainer?profile=maintainer (the launcher prints it; see [the dashboard](#the-dashboard)) |
| Leave | Close the chat. The sandbox keeps running, idle |
| Stop the sandbox | `openshell sandbox stop assistant-maint` (`assistant` starts it again) |
| See whether it is running | `openshell sandbox list` |
| See what it contacted, and what was blocked | `openshell logs assistant-maint` |
| Update to a new version | `assistant --upgrade` (from a clone: `git pull`, then `container/assistant --build --upgrade`). Keeps your memory, history, sign-in and model |
| Replace your GitHub token | `bash -c 'read -rs -p "New GitHub token: " REPO_GITHUB_TOKEN; echo; export REPO_GITHUB_TOKEN; openshell provider update kyverno-repo-github --credential REPO_GITHUB_TOKEN'`, then open a new chat. Chats already open keep the old token |
| Use another model provider | `assistant --provider anthropic` (or `openai`, `nvidia`) |
| Set up without opening the chat | `assistant --no-attach` |

## Where your data lives

Your memory, chat history, notes, settings and the model sign-in live **inside the sandbox**, in the profile
folder `/opt/hermes-home/.hermes/profiles/maintainer`. They survive stopping and starting it, and
`assistant --upgrade` carries them to a new image. Deleting the sandbox deletes them.

Your GitHub token is not in the sandbox: it lives in OpenShell, on your laptop. The Copilot sign-in token is in
the sandbox (it only grants access to Copilot's models).

What the sandbox can reach on the internet, and nothing else:

| Destination | Why |
|---|---|
| `api.github.com` (reads only) | The PR and issue data |
| The GitHub MCP server (`api.githubcopilot.com/mcp/`) | The tools the agent uses to look things up and to act, with your token added on the way out. The gateway lets through only the tools on the allowlist in `config.yaml` |
| GitHub Copilot's model service | The model, when you use Copilot. Only its inference paths, not `/mcp/` |
| `github.com` sign-in pages | Only the two device-code endpoints, for the Copilot sign-in |
| `github.com`, `codeload.github.com`, `raw.githubusercontent.com` (read-only) | Lets the agent's terminal read public code |
| `models.dev` (read-only) | The list of models for `/model` |
| A model provider you added | Only providers whose key you stored with `--provider` |

## The dashboard

A browser board that shows what the agent just told you, linked, grouped and sortable, so you don't scroll the
chat or copy links by hand. It runs inside the sandbox, and the launcher forwards a port on your laptop to it and
prints the address:

```
http://127.0.0.1:9219/maintainer?profile=maintainer
```

Pick **Maintainer** in the sidebar under Plugins. It refreshes every 15 seconds. Keep it open next to the chat: it fills
when the agent answers a queue, stale-PR, issue-triage or Discussions request, not in the background.

| Tab | Shows |
|---|---|
| **Overview** | What is waiting on you, grouped by verdict (rescue, decide, review, close, triage…), the milestone and due date, gate status, recent actions. |
| **PRs / Issues / Discussions** | One row per item with a GitHub link, state (CI, merge state, size, labels) and the agent's verdict and reason. Switch views, filter by verdict, search, sort. |
| **Detail panel** | Summary, unresolved threads, a graph of related PRs and issues, and any drafted comment awaiting your confirmation, with a copy button. |
| **Activity** | Every action you confirmed (approvals, comments, nudges, closes, deferrals) with a timestamp and a link. |

The Maintainer tab is read-only. Confirming and acting still happen in the chat. It is reachable only from your own
laptop (the port is forwarded to `127.0.0.1` explicitly), and port 9219 is used so it does not clash with a Hermes
dashboard on 9119. Set `ASSISTANT_DASHBOARD_PORT` to change it. Hermes protects the dashboard itself: a session token
that changes on every start, a check that the request's `Host` matches the address it listens on (against DNS
rebinding), and cross-origin requests only from localhost. It is still Hermes' whole dashboard, not just our tab: its
Settings and chat pages are served too, and the Settings pages can show what is in the sandbox's settings file, which
includes the Copilot sign-in token. Your GitHub token is not there. If you do not want it, stop it with
`openshell forward stop 9219` (the launcher starts it again next time you run it).

## Coding in the sandbox

Both profiles have a terminal inside the sandbox, which cannot reach your laptop. Ask the assistant to reproduce an
issue or fix a PR and it follows the `workspace` skill: `assistant-workspace <owner/repo>` clones the repo and downloads
the toolchains it declares (Go, Node, or the Kyverno CLI and Chainsaw), then it builds, runs the tests, and shows you
the real output. The toolchains are downloaded the first time, not baked into the image: the network policy allows
only those hosts, GET only, and every download is checked against the vendor's checksum. Caches live in the sandbox, so
recreating it downloads them again, unless you pass `--keep-caches` to `--upgrade`. Your cloned repositories and their
unpushed commits always come along on `--upgrade`. When a toolchain's rough memory need is above what the sandbox really
has, `assistant-workspace` says so with both numbers. Things that need a real cluster cannot run, and the assistant says so
(see [#25](https://github.com/kyverno/assistant/issues/25)).

The result comes back as a patch (`openshell sandbox download`), or as a pushed branch if you turn on pushing:

```bash
assistant --push-to-fork      # off by default; --no-push removes it
```

That stores a second credential in OpenShell (your `gh` sign-in's token, or a token with write on your fork). OpenShell
lets `git push` reach only your own forks (and, as a maintainer, the repository you manage); a hook refuses force pushes,
branch deletes and pushes to main. Nothing is ever merged.

## Slack and scheduled jobs

These are not available in the sandbox yet. They work in the [host install](host-install.md):

- **Slack**: talk to the assistant by mentioning it, and it can read and post in your maintainers channel.
- **Scheduled jobs**: a weekday review digest and two memory jobs (installed paused).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `The requested model is not supported` | The model is not available on your Copilot plan. `/model gpt-4.1 --global` |
| `No LLM provider configured ... openrouter` | You picked a mixture-of-agents option that needs an OpenRouter key. Pick a normal model with `/model` |
| The chat looks frozen right after start | Give it 30 seconds. If it stays blank, press Enter once. If it still does nothing, run `assistant --upgrade` |
| `could not pull ...` | No network to the registry, or a version that does not exist. Check the tag, or use `assistant --image` with a tag that exists. A pull that crawls is usually your route to GitHub's download servers: try another network, the finished layers are kept |
| The sandbox goes to `Error`, "UID or GID zero" | The image's default user must be 1000, not root. Rebuild with `--build` |
| `could not register the provider profile` | OpenShell holds an older copy of this project's profile from an earlier run. Start clean (nothing is rebuilt): `openshell sandbox delete assistant-maint`, `openshell provider delete kyverno-repo-github`, `openshell profile delete kyverno-repo-github`, then `assistant --image assistant:dev` (or without `--image` once an image is published). The launcher asks for your GitHub token again |
| `sandbox ... did not become Ready` | The launcher prints the sandbox's state and the last lines of its log. Retry from scratch: `openshell sandbox delete assistant-maint`, then run the launcher again |
| The build is very slow or runs out of memory | Docker Desktop's VM memory. Raise it in Settings → Resources (at least 4 GB) |
| `Docker Desktop ... is older than 4.60` | The sandbox needs a newer Docker Desktop. Update it |
| GitHub says the token is rejected | Wrong, expired or revoked: create a new one. A 404 means the token cannot see that repo (scope it to the repo, or have the org approve it) |

## Removing everything

```bash
openshell sandbox delete assistant-maint          # the sandbox and everything in it (memory, history, sign-in)
openshell provider delete kyverno-repo-github       # your stored GitHub token
openshell provider delete kyverno-anthropic         # model keys you added, if any (also kyverno-openai, kyverno-nvidia)
docker rmi assistant:dev                    # the image, if you built it yourself
```

If you used `--push-to-fork`, also `openshell provider delete kyverno-git-push` (or run the launcher with `--no-push`). With `--no-openshell`, the clones and toolchains live in a Docker volume that outlives the container:
`docker volume rm assistant-maint-docker-workspace` (the name follows the container's).
