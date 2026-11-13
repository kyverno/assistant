# Getting started

**You do not clone this repository.** The assistants ship as a signed container image on GHCR
(`ghcr.io/kyverno/assistant`). One command downloads the launcher from that image, runs it, and removes the copy, so the
launcher and the image it starts always come from the same release. You need Docker, a GitHub account, and a model
(GitHub Copilot by default). Everything else, including OpenShell, is installed or pulled for you.

The [README](../README.md) explains what the assistants do and why they are safe; this page is about getting one running,
and keeping it running.

- [The installer, step by step](#the-installer-step-by-step)
- [Signing in: GitHub and Copilot](#signing-in-github-and-copilot)
- [Which assistant do I want?](#which-assistant-do-i-want)
- [Case 1: a Mac with Apple Silicon (recommended)](#case-1-a-mac-with-apple-silicon-recommended)
- [Case 2: Linux](#case-2-linux)
- [Case 3: an Intel Mac, or Docker only](#case-3-an-intel-mac-or-docker-only)
- [Case 4: building from a clone](#case-4-building-from-a-clone)
- [Tokens](#tokens)
- [For an organisation: which repositories, which tokens](#for-an-organisation-which-repositories-which-tokens)
- [Choosing a model](#choosing-a-model)
- [Letting it push to your fork](#letting-it-push-to-your-fork)
- [Upgrading](#upgrading)
- [Starting over and removing everything](#starting-over-and-removing-everything)
- [Troubleshooting](#troubleshooting)

## The installer, step by step

```bash
curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh
```

Give options after `sh -s --`, for example `... | sh -s -- --profile contributor`. To make it a command, put this in your
shell profile:

```bash
alias assistant='curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh -s --'
```

Then type `assistant`, `assistant --profile contributor`, `assistant --upgrade`. **In the rest of the docs, `assistant`
means this** (or `container/assistant` if you work from a clone, which you do not need to).

**Before you start:** Docker running; a GitHub account that can read the repository; a Copilot subscription or another model
key. The script needs a terminal, because it asks questions.

It prints five numbered steps. This is what each one does, and what you do:

| Step | What happens | You |
|---|---|---|
| **1. Docker and OpenShell** | Checks that Docker runs. Installs [OpenShell](https://github.com/NVIDIA/OpenShell) (its CLI and a background gateway) if it is missing | Nothing, unless Docker is not running |
| **2. Image** | Pulls `ghcr.io/kyverno/assistant` the first time, about 1.2 GB compressed. Later runs reuse it | Wait a few minutes |
| **3. GitHub token** | Asks which assistant you want if you did not say (**1 maintainer**, **2 contributor**). Asks which repository, as `owner/repo`: there is no default. Asks how to authorize GitHub (see below). Checks the token against the repository, then stores it **in OpenShell, outside the sandbox** | Type the repository, then sign in |
| **4. Sandbox** | Creates the sandbox from the image and applies the network policy. The first time takes a minute or two | Wait |
| **5. Assistant** | Starts inside the sandbox. It reads your GitHub username from the token, then starts the **GitHub Copilot sign-in**, which prints a code and a page. Then you pick a model | Open <https://github.com/login/device>, enter the code, approve, pick a model |

The chat opens after about 30 seconds (`cpuinfo` and `onnxruntime` lines before it are harmless). **Only the first run asks
questions.** Next time, `assistant` takes you straight back in, and `assistant --upgrade` moves to a newer image while
keeping your memory, chat history, model choice, Copilot sign-in and cloned repositories.

## Signing in: GitHub and Copilot

There are two sign-ins, both in your browser, and they do different jobs.

**1. GitHub, so the assistant can read your repository (step 3).** Pick one:

- **Sign in with GitHub (`gh`)**: if the GitHub CLI is installed it opens a browser sign-in; if you are already signed in
  it just uses that. Nothing to create. The catch: `gh` always asks for broad scopes, so the token can act on every
  repository you can reach, private ones included. The assistant still cannot merge.
- **Paste a fine-grained token**, scoped to the one repository. It is hidden as you type. This is the tighter option;
  [Tokens](#tokens) lists the permissions.

Either way the token is stored in OpenShell on your machine. The sandbox only ever holds a placeholder, and OpenShell's
proxy swaps the real token in on the way out to GitHub.

**2. GitHub Copilot, so the assistant has a model to think with (step 5).** It is the standard device-code login:

1. The launcher prints a **one-time code** and a link: <https://github.com/login/device>.
2. Open the link in a browser on your laptop (the sandbox has no browser), enter the code, and approve.
3. The launcher says `Copilot signed in`, then shows a model picker. Choose a model. Which ones appear depends on your plan.

Hermes keeps that Copilot token in its own home inside the sandbox, and the network policy only lets it reach Copilot.
Later runs do not ask again; `/model` inside the chat switches models.

### Why Copilot

The people this is built for already have it. **CNCF gives GitHub Copilot Enterprise to official CNCF project maintainers**
(they request it through the CNCF Service Desk;
[announced December 2025](https://contribute.cncf.io/blog/2025/12/16/github-copilot-enterprise-for-maintainers/)), and
**GitHub gives Copilot Pro free to maintainers of popular open-source projects**
([eligibility](https://docs.github.com/en/copilot/how-tos/manage-your-account/get-free-access-to-copilot-pro)). So there is
no API key to buy, paste or leak: sign in once and go.

**No Copilot?** Start with `assistant --provider anthropic` (or `openai`, `nvidia`). The launcher asks for that key once
and stores it in OpenShell, like the GitHub token. See [Choosing a model](#choosing-a-model).

## Which assistant do I want?

One image holds two assistants. Each has its own sandbox, its own GitHub token and its own memory.

| | **Maintainer** (`--profile maintainer`) | **Contributor** (`--profile contributor`) |
|---|---|---|
| For | People who review and merge a repository's pull requests | People contributing to a repository (configured for Kyverno today) |
| Does | Builds your PR queue, triages issues, drafts reviews and follow-ups, proposes a merge order | Finds issues worth doing, explains the code and what earlier PRs decided, helps prepare PRs, explains review feedback |
| Can write to GitHub | Comments, labels, reviews, after you confirm each one | A comment on an issue or PR, and a pull request from your own fork, after you confirm the text |
| Never | Merges (refused by the proxy, whatever the token can do) | Merges, approves, submits a formal review |
| Token | Fine-grained, scoped to the repo ([details](#tokens)) | Your own account's token, or a `gh` sign-in |
| Dashboard | Yes (`http://127.0.0.1:9219/maintainer?profile=maintainer`) | No |

If you run the launcher without `--profile` it picks for you: the one you already have a sandbox for, otherwise it
asks (1 maintainer, 2 contributor). Both can coexist; add `--profile` to choose.

Both assistants can also clone a repository, build and test it in the sandbox and hand you the result (see
[Coding in the sandbox](usage.md#coding-in-the-sandbox)).

## Case 1: a Mac with Apple Silicon (recommended)

This is where everything was developed and tried.

**You need:** [Docker Desktop](https://docs.docker.com/get-docker/) 4.60 or newer, running, with at least 4 GB of
memory (Settings → Resources); about 20 GB of free disk; a way to authorize GitHub ([Tokens](#tokens)); and a model
([Choosing a model](#choosing-a-model)). You do not install Hermes, Python or OpenShell: the launcher installs
OpenShell if it is missing.

**1. Run it.** Follow [The installer, step by step](#the-installer-step-by-step) and [Signing in](#signing-in-github-and-copilot).
To use a fixed version instead of `latest`, add `--image ghcr.io/kyverno/assistant:v0.1.0` after `-s --`.

**2. Check it works.** A good first question for the contributor is *"Use find_issues to list 3 open issues I could
work on."*; for the maintainer, *"what's my PR queue"*. In the start banner, the `github` server should be listed and
many tools should show.

## Case 2: Linux

The same commands as [Case 1](#case-1-a-mac-with-apple-silicon-recommended). What is different:

- The kernel must support Landlock: Linux 5.13 or newer.
- Docker Engine works instead of Docker Desktop.
- **Verified on a CI runner:** on a GitHub Linux runner (Ubuntu 24.04, kernel 6.17, Docker Engine 28, x86_64) an image
  was built and the whole stack ran: the token stayed a placeholder, the proxy refused a merge, the hooks were read-only
  and the dashboard answered. That check ran before the contributor profile, the coding environment and
  `--push-to-fork` existed, so those are untested on Linux.
- **Not tried** on a real Linux laptop, and the launcher has not been tried on arm64 Linux, although the image is
  built for it.

## Case 3: an Intel Mac, or Docker only

OpenShell supports macOS on Apple Silicon only, so on an Intel Mac, or if you do not want OpenShell, use plain
Docker:

```bash
assistant --no-openshell --profile contributor
```

**This is weaker.** There is no network allowlist (the container can reach the internet), the GitHub token and
model keys are real values in the container, the proxy does not refuse merges (so use a fine-grained token, not the
`gh` sign-in), there is no audit log, and `--push-to-fork` is refused. The terminal is off unless you add
`--allow-terminal`. Details and the full comparison table: [docs/container.md](container.md#running-without-openshell).
Not tried on Intel.

Windows: OpenShell supports it through WSL2 (experimental). Run the launcher inside WSL2. Untested.

## Case 4: building from a clone

For people changing the assistant itself, or who want to build the image on their own machine:

```bash
git clone https://github.com/kyverno/assistant.git
cd assistant
container/assistant --build --profile contributor        # the first build takes 15 to 30 minutes and downloads about 5 GB
```

`--build` needs the checkout, so it does not work from the launcher extracted from an image. Add `--upgrade` when you
rebuild, otherwise an existing sandbox keeps running the image it was made from.

## Tokens

The launcher asks how to authorize GitHub the first time, and stores the result in OpenShell on your machine. The
sandbox only holds a placeholder.

| Option | What it is | When to use |
|---|---|---|
| **1. `gh` sign-in** (default) | Uses the GitHub CLI's login, or opens the browser sign-in if you are not signed in | Easiest. The token can act on every repository you can, and `gh` asks for `repo`, `read:org` and `gist`. The assistant still cannot merge |
| **2. A pasted token** | A fine-grained personal access token, hidden as you type | When you want it limited to one repository |

**Maintainer token** (fine-grained, scoped to the repo): Contents Read; Pull requests Read & Write; Issues Read &
Write; Checks Read; Code scanning alerts Read; Dependabot alerts Read; Secret scanning alerts Read; Organization
members Read. **Do not grant Contents Write or any merge or admin scope.** Optional extras and the reasoning:
[README step 1](setup.md#1-create-a-github-token).

**Contributor token:** a fine-grained token of your own account; "Public repositories (read-only)" is enough to read
everything. To comment on an issue, or to open a pull request from your fork, the token must be able to write there
(or use the `gh` sign-in, which can).

Wrong, expired or revoked? A 404 means the token cannot see the repo. Replace it with `--reset`.

## For an organisation: which repositories, which tokens

An organisation publishes `assistant-policy.yaml` at the root of its `.github` repository
([example](../container/assistant-policy.yaml)). When you enter `acme/api`, the launcher looks for
`acme/.github/assistant-policy.yaml` and asks `Use it?`; answer `n` to go without, or type another location (an https
URL or a file). If it finds none, it asks whether you have one. The answer is remembered; you are asked again only if the
organisation changes the file. To skip the questions, set `ASSISTANT_POLICY` to a file or https URL, or put the file in your
own image at `/opt/kyverno/launcher/assistant-policy.yaml`.

Keys, all optional:

- `repos`: the `owner/repo` patterns (`acme/*`, `acme/api`) the assistant may be started on. Any other is refused.
- `default_repo`, `fork_repos`: what to offer when asking, and which fork repositories `--push-to-fork` allows.
- `token: fine-grained`: refuses a token that can act beyond selected repositories (the `gh` sign-in, a classic token) and
  does not offer the `gh` sign-in.

A policy that cannot be read, or is malformed, stops the launcher: it never falls back to no limits. The check runs on your
machine, so it stops mistakes, not someone who edits their own copy. What the agent can do once it runs is limited by
OpenShell's policies, not by this file: see the [README](../README.md#protected-by-openshell).

## Choosing a model

- **GitHub Copilot** is the default; the sign-in is one click. Which models answer depends on your plan. If the picker's
  choice fails with `The requested model is not supported`, type `/model gpt-4.1 --global` in the chat (`gpt-4.1`
  and `gpt-4o-mini` answered on a free account).
- **A small model works.** The assistant is set up so every tool is visible to the model without searching. If you hit
  a rate limit, it ends in an API error and a retry; wait and try again.
- **Another provider:** `assistant --provider anthropic` (or `openai`, `nvidia`). The launcher asks for the key once and
  stores it in OpenShell. Then choose a model with `/model`.

## Letting it push to your fork

Off by default. Without it, the assistant commits in the sandbox and hands you a patch to apply and push yourself.
With it, it can run `git push` to branches of **your own fork** of the repository you named (`ASSISTANT_REPO`; set
`ASSISTANT_PUSH_REPOS` for more) and, as a maintainer, to the repo you manage, and nowhere else:

```bash
ASSISTANT_PUSH_REPOS="repo-a repo-b" container/assistant --profile contributor --push-to-fork
```

It asks for a push token once (your `gh` sign-in, or a fine-grained token with Contents Read and write on your fork)
and stores it in OpenShell. The sandbox never sees it. Force pushes, deletes and pushes to `main` are refused. To turn
it off again: `--no-push`. Not available with `--no-openshell`.

## Upgrading

Your memory, chat history, model choice, Copilot sign-in and cloned repositories are kept.

```bash
curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh -s -- --upgrade
```

This takes the launcher from the new image, pulls the image and recreates the sandbox. Add `--keep-caches`
to also carry the downloaded toolchains and build caches (large). From a clone: `git pull`, then
`container/assistant --build --upgrade`.

## Starting over and removing everything

| To | Run |
|---|---|
| Forget the stored GitHub token | `assistant --reset` |
| Stop the sandbox | `openshell sandbox stop assistant-contrib` (the launcher starts it again) |
| Delete a sandbox and everything in it | `openshell sandbox delete assistant-contrib` (maintainer: `assistant-maint`) |
| Delete the stored GitHub token | `openshell provider delete assistant-contributor-github` (maintainer: `assistant-repo-github`) |
| Delete a model key | `openshell provider delete assistant-anthropic` (or `-openai`, `-nvidia`) |
| Delete the push token | `openshell provider delete assistant-git-push` |
| Delete the image | `docker rmi ghcr.io/kyverno/assistant:latest` |

With `--no-openshell`, the clones and toolchains live in a Docker volume that outlives the container:
`docker volume rm assistant-contrib-docker-workspace` (the name follows the container's).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `docker pull` crawls or stalls | Your route to GitHub's download servers is slow. Check with another network (a phone hotspot); layers already downloaded are kept, so run the pull again |
| `…already exists and keeps running the image it was made from` | The sandbox is from an older image. Add `--upgrade` |
| Started the wrong assistant | It tells you which it chose. Pass `--profile maintainer` or `--profile contributor` |
| `--build needs a checkout of the repository` | You are using the launcher extracted from an image. Use [Case 4](#case-4-building-from-a-clone), or drop `--build` |
| `could not tell whose forks to allow` | An older launcher. Refresh it (see [Upgrading](#upgrading)); or set `ASSISTANT_PUSH_LOGIN` to your username |
| The banner says `github … connecting` | Normal for a moment: it connects after the banner is drawn |
| The agent cannot find `find_issues` or the GitHub tools | An older image hides them from the model. Upgrade |
| The agent runs `git clone` and then cannot push | An older image. Upgrade; the assistant should use `assistant-workspace owner/repo` |
| The dashboard address does not answer | Only the maintainer assistant has a dashboard. If another sandbox left a dead forward, `openshell forward stop 9219` |
| The chat looks frozen after start | Give it 30 seconds, press Enter once, then try `--upgrade` |
| `Docker Desktop … is older than 4.60` | Update Docker Desktop |
| A build is very slow or runs out of memory | Raise Docker's memory (Settings → Resources) to at least 4 GB |
| `sandbox … did not become Ready` | The launcher prints its state and log. Delete the sandbox and run the launcher again |
| `could not register the provider profile` | OpenShell holds an older copy. Delete the sandbox, the provider and `openshell profile delete assistant-repo-github` (or `-contributor-github`), then run again |

More: the [README troubleshooting table](usage.md#troubleshooting) and [docs/container.md](container.md).

## Checking what you downloaded

Every release image is signed. To verify it came from this repository's release workflow:

```bash
cosign verify ghcr.io/kyverno/assistant:v0.1.0 \
  --certificate-identity https://github.com/kyverno/assistant/.github/workflows/container-image.yml@refs/tags/v0.1.0 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
