# Setting it up, step by step

Everything the quick start in the [README](../README.md#set-it-up) skips: what you need, creating the GitHub token, starting it, choosing a model. For other platforms see [`getting-started.md`](getting-started.md).

## Setup, step by step

### 0. What you need

| Need | Details |
|---|---|
| **A Mac with Apple Silicon**, or Linux | Tested on Apple Silicon only. See [what runs on which machine](#what-runs-on-which-machine). |
| **[Docker Desktop](https://docs.docker.com/get-docker/) 4.60 or newer** | Must be running. Give Docker at least 4 GB of memory (Docker Desktop → Settings → Resources); an 8 GB Mac is enough. |
| **About 20 GB of free disk** | The image is about 4.2 GB. |
| **GitHub access** | The GitHub CLI (`gh`) signed in, or a token: [Step 1](#1-create-a-github-token). |
| **A model** | A GitHub Copilot account (the free plan works with a few models), or an Anthropic, OpenAI or NVIDIA API key. See [step 4](#4-pick-a-model-that-works). |

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
| **Intel Mac** | Not supported with OpenShell (it supports macOS on Apple Silicon only). `container/assistant --no-openshell` needs only Docker, with less protection (not tried on Intel): see [docs/container.md](container.md#running-without-openshell) |
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
See [`docs/architecture.md`](architecture.md) for the reasoning. Keep the token handy; you paste it in step 3.

### 2. Get the image

Pick one:

| Option | Command | Time | Notes |
|---|---|---|---|
| **Build it yourself** | `container/assistant --build` | 15 to 30 minutes, once | Works today on any supported machine. Downloads about 5 GB while building. |
| **Download the published image** | `container/assistant` | A few minutes (about 1.2 GB) | The default. It pulls `ghcr.io/kyverno/assistant:latest` (see [why](how-it-works.md#why-it-is-built-this-way)); pin a version with `--image ghcr.io/kyverno/assistant:v0.1.0`. |

### 3. Start it

```bash
container/assistant --build        # or just: container/assistant
```

It prints five numbered steps. This is what each one does and what you answer:

| Step | What happens | You do |
|---|---|---|
| **1. Docker and OpenShell** | Checks Docker is running; installs OpenShell if missing | Nothing, unless Docker is not running |
| **2. Image** | Builds or pulls the image | Wait |
| **3. GitHub token** | Asks which repo to manage, then how to authorize: sign in with `gh` (browser), or paste a token (hidden). It checks the token against that repo before keeping it, and stores it in OpenShell | Type the repo (`owner/repo`), then sign in or paste the token |
| **4. Sandbox** | Creates the sandbox and applies its network policy (a minute or two the first time) | Wait |
| **5. Assistant** | Takes your GitHub username from the token (it asks only if it cannot read it). Then GitHub Copilot sign-in: it prints a page and a code | Open https://github.com/login/device in your browser, enter the code, approve. Then pick **GitHub Copilot** and a model in the picker |

The chat then opens. The screen takes about 30 seconds to appear, because the memory plugin loads its search
engine; you may see some `cpuinfo` and `onnxruntime` lines first, which are harmless. The first run is the only
one that asks questions.

### 4. Pick a model that works

The assistant needs a model. Copilot is the default because the sign-in is one click.

- **GitHub Copilot.** Which models answer depends on your plan. A free account may reject the model the picker
  pre-selects with `The requested model is not supported`, even when Copilot's own list marks it enabled. If that
  happens, switch inside the chat:
  ```
  /model gpt-4.1 --global
  ```
  `gpt-4.1` and `gpt-4o-mini` answered on a free account. `--global` keeps the choice for next time.
- **Another provider.** Its API key is stored in OpenShell like the GitHub token, so the sandbox never sees it:
  ```bash
  container/assistant --provider anthropic      # or openai, or nvidia
  ```
  The launcher asks for the key once. Then use `/model` to pick one of its models.

### 5. Talk to it

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
