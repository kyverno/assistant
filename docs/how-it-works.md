# How it works

The pieces, the safety layers in full, and why it is built this way.

## How it works

```
 your laptop
 ┌────────────────────────────────────────────────────────────────────────────┐
 │                                                                            │
 │   you ──► container/assistant ──► OpenShell gateway                          │
 │           (the launcher)         • keeps your GitHub token (and any model  │
 │                                    API key) so the sandbox never holds it  │
 │                                  • a proxy that allows only listed hosts   │
 │                                    and adds the token on the way out       │
 │                                         │                                  │
 │              ┌──────────────────────────▼───────────────────────────────┐  │
 │              │  the sandbox: one Docker container, built from the image │  │
 │              │   Hermes + this profile + memory + the agent's terminal  │  │
 │              │   holds only a placeholder where your token would be     │  │
 │              └───────────────────────────┬──────────────────────────────┘  │
 └──────────────────────────────────────────┼─────────────────────────────────┘
                                            ▼
                      GitHub (reads; the GitHub MCP server)    Copilot or another model
```

Three pieces, and each one has a job:

- **The image** is a ready-made copy of everything the agent needs: Hermes at a pinned version, this profile,
  its memory plugin and the models it uses. Nothing is installed on your laptop and nothing is downloaded when
  you chat. It is built once ([why](#why-it-is-built-this-way)).
- **The sandbox** is that image running inside OpenShell. The agent works here: it reads GitHub, thinks, remembers,
  and can run commands, but only inside this container, which has no access to your files.
- **The OpenShell gateway** sits between the sandbox and the internet. It holds your GitHub token and, when the
  sandbox calls GitHub, it adds the real token on the way out. If the agent were tricked into printing its
  credentials, it would print a meaningless placeholder. The gateway also refuses everything not on an allowlist,
  including merging.

The launcher, `container/assistant`, ties them together. Each run it checks Docker, installs OpenShell if needed,
stores your token, creates or starts the sandbox, and opens the chat.

## How it stays safe

The agent cannot merge, and no single mechanism is trusted on its own. Five layers, and the ones that matter most
are enforced by OpenShell outside the sandbox, where the agent cannot edit them:

1. **The token has no merge permission** (Pull requests: write, but not Contents: write). GitHub itself refuses.
2. **The gateway checks every GitHub call.** On `api.github.com` it allows reads only and refuses merges, repository
   deletion and GraphQL mutations. On the GitHub MCP server it checks each tool call against the allowlist and
   refuses everything else, plus anything named like merge, delete or force-push, even inside a batched request.
3. **The tool allowlist in `config.yaml`**: the GitHub tool list has no merge tool.
4. **A hook** blocks any tool whose name looks like merge, delete or force-push.
5. **The network allowlist** denies everything not listed above.

Layers 1, 2 and 5 do not depend on any file inside the sandbox. Layers 3 and 4 do: the agent has a terminal and file
tools, so a manipulated agent could edit its own `config.yaml` (the hook scripts are read-only to it). Layers 1, 2 and
5 are what you rely on then.
Your token never enters the sandbox, so a prompt-injected agent cannot leak it. The details, including exactly what
was tested, are in [`docs/container.md`](container.md).

## Why it is built this way

**Why a sandbox at all?** An assistant that reads issues and PR comments reads text written by strangers. A
sandbox means that even a successfully manipulated agent can only do what the sandbox allows: no access to your
files, no token to steal, only the listed hosts.

**Why one prebuilt image?** The agent is built from many moving parts (Hermes, Python packages, a memory plugin,
a search model). In a sandbox with no open internet, anything the agent tries to download or install the first
time it runs would fail, and some of those steps pop up a question the chat screen hides. So everything is installed
and checked when the image is built, including a full memory cycle run with the network switched off. A new
user's first run takes about a second of setup, and every machine runs identical bits.

**Why publish the image to ghcr.io?** `ghcr.io` is GitHub's container registry. Building the image on your own
laptop takes 15 to 30 minutes, downloads about 5 GB, and can break if a package was released since the last
person built it. Publishing it once from CI, from a tagged release, means a new maintainer just downloads it,
and an upgrade is a new tag. The workflow is `.github/workflows/container-image.yml`; it builds for Apple Silicon
and Intel and publishes one image. Until a release is published, use `--build`.

**Why does the first build take so long?** It downloads Hermes and its tools, the memory plugin, and a 64 MB
search model, then runs the proofs above. Later builds reuse Docker's cache and take seconds.

**Why no Slack or scheduled jobs yet?** Slack's tool runs under Docker, which a sandbox does not have, and the
scheduled jobs run inside Hermes' gateway, which is not started. They work in the
[host install](host-install.md).
