# Setup details

What you need, which machines work, how to create the GitHub token, and some things to ask. The install itself is in [`getting-started.md`](getting-started.md).

## Setup, step by step

### 0. What you need

| Need | Details |
|---|---|
| **A Mac with Apple Silicon**, or Linux | Tested on Apple Silicon only. See [what runs on which machine](#what-runs-on-which-machine). |
| **[Docker Desktop](https://docs.docker.com/get-docker/) 4.60 or newer** | Must be running. Give Docker at least 4 GB of memory (Docker Desktop → Settings → Resources); an 8 GB Mac is enough. |
| **About 20 GB of free disk** | The image is about 4.2 GB. |
| **GitHub access** | The GitHub CLI (`gh`) signed in, or a token: [Step 1](#1-create-a-github-token). |
| **A model** | A GitHub Copilot account (the free plan works with a few models), or an Anthropic, OpenAI or NVIDIA API key. See [choosing a model](getting-started.md#choosing-a-model). |

You do not install Hermes, Python or OpenShell yourself. If OpenShell is missing, the launcher installs it using
OpenShell's own installer.

### What runs on which machine

The image is built for both chip families (Apple Silicon / arm64, and Intel / AMD, called amd64) and passes its
build-time checks on both. What decides whether the whole thing runs is Docker, OpenShell and the launcher around it.

| Your machine | Status |
|---|---|
| **Mac with Apple Silicon** | Tested: this is where everything was developed and checked, including real chats |
| **Linux, x86_64** | Checked end to end on a GitHub Linux runner (Ubuntu 24.04, kernel 6.17, Docker Engine 28): OpenShell installed, the sandbox started, the token stayed a placeholder, merges were refused by the proxy, the hooks were read-only and the dashboard answered. Not tried on a real Linux laptop. The kernel needs Landlock (Linux 5.13 or newer) |
| **Linux, arm64** | The image is built for it; the launcher has not been tried there |
| **Intel Mac** | Not supported with OpenShell (it supports macOS on Apple Silicon only). `assistant --no-openshell` needs only Docker, with less protection (not tried on Intel): see [docs/container.md](container.md#running-without-openshell) |
| **Windows** | OpenShell supports it through WSL2 (experimental); run the launcher inside WSL2. Untested |

The launcher checks your machine first and says so plainly if it is one it cannot support.

### 1. Create a GitHub token

> **Have the GitHub CLI (`gh`)?** You can skip this step. The launcher asks how to authorize GitHub. Choose **1**
> and it uses your `gh` sign-in, or opens the browser sign-in (`gh auth login`) if you are not signed in yet. Choose
> **2** to paste the fine-grained token below instead. `gh` always asks for the `repo`, `read:org` and `gist`
> scopes, so its token can act on every repository you have access to, private ones included, while the
> fine-grained one is limited to the repo you scope it to. `gh` may also offer to set up git
> credentials on your machine; you can answer No. The token is stored in OpenShell like any other, and the
> assistant still cannot merge.

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

**Do not grant Contents Write, merge, or admin scopes.** Without Contents Write, GitHub itself will not let this
token merge a pull request, which is one of the [layers that stop the assistant merging](how-it-works.md#how-it-stays-safe).
See [`docs/architecture.md`](architecture.md) for the reasoning. Keep the token handy; you paste it when the installer asks.

### 2. Install, sign in, pick a model

Nothing to clone: one command pulls the image and runs the launcher. The installer's steps, the GitHub and Copilot sign-ins
(and why Copilot), choosing or switching a model, and upgrading are in [`getting-started.md`](getting-started.md#the-installer-step-by-step).

### 3. Talk to it

Just ask. For example (more in [`docs/capabilities.md`](capabilities.md)):

```
what's my PR queue
explain PR #4181
which Dependabot PRs should I review and merge?
does #4172 need anything from me right now?
which stale PRs should I close or reassign?
tell me about issue #4512
```

It reads GitHub and answers. When you ask it to act (label, comment, approve), it asks you to confirm first.
Type `/help` for the chat commands.
