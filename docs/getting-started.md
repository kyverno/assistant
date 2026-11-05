# Getting started

Pick the case that matches you. Each one lists the exact commands, what the launcher asks, and what you should see.
The [README](../README.md) explains how the assistant works and why it is safe; this page is only about getting it
running.

In the commands below, `kyverno` means the launcher: what the install command starts, or
`container/kyverno` in a clone. Give the options after `sh -s --`.

- [Which assistant do I want?](#which-assistant-do-i-want)
- [Case 1: a Mac with Apple Silicon (recommended)](#case-1-a-mac-with-apple-silicon-recommended)
- [Case 2: Linux](#case-2-linux)
- [Case 3: an Intel Mac, or Docker only](#case-3-an-intel-mac-or-docker-only)
- [Case 4: building from a clone](#case-4-building-from-a-clone)
- [Tokens](#tokens)
- [Choosing a model](#choosing-a-model)
- [Letting it push to your fork](#letting-it-push-to-your-fork)
- [Upgrading](#upgrading)
- [Starting over and removing everything](#starting-over-and-removing-everything)
- [Troubleshooting](#troubleshooting)

## Which assistant do I want?

One image holds two assistants. Each has its own sandbox, its own GitHub token and its own memory.

| | **Maintainer** (`--profile maintainer`) | **Contributor** (`--profile contributor`) |
|---|---|---|
| For | People who review and merge a repository's pull requests | People contributing to Kyverno |
| Does | Builds your PR queue, triages issues, drafts reviews and follow-ups, proposes a merge order | Finds issues worth doing, explains the code and what earlier PRs decided, helps prepare PRs, explains review feedback |
| Can write to GitHub | Comments, labels, reviews, after you confirm each one | A comment on an issue or PR, and a pull request from your own fork, after you confirm the text |
| Never | Merges (refused by the proxy, whatever the token can do) | Merges, approves, submits a formal review |
| Token | Fine-grained, scoped to the repo ([details](#tokens)) | Your own account's token, or a `gh` sign-in |
| Dashboard | Yes (`http://127.0.0.1:9219/kyverno?profile=kyverno`) | No |

If you run the launcher without `--profile` it picks for you: the one you already have a sandbox for, otherwise it
asks (1 maintainer, 2 contributor). Both can coexist; add `--profile` to choose.

Both assistants can also clone a repository, build and test it in the sandbox and hand you the result (see
[Coding in the sandbox](../README.md#coding-in-the-sandbox)).

## Case 1: a Mac with Apple Silicon (recommended)

This is where everything was developed and tried.

**You need:** [Docker Desktop](https://docs.docker.com/get-docker/) 4.60 or newer, running, with at least 4 GB of
memory (Settings → Resources); about 20 GB of free disk; a way to authorize GitHub ([Tokens](#tokens)); and a model
([Choosing a model](#choosing-a-model)). You do not install Hermes, Python or OpenShell: the launcher installs
OpenShell if it is missing.

**1. Run it.** No clone and no folder: the script takes the launcher from the image and runs it.

```bash
curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh -s -- --profile contributor      # or --profile maintainer
```

The first run downloads the image, about 1.2 GB compressed, so give it a few minutes. To use a fixed version instead
of `latest`, add `--image ghcr.io/kyverno/assistant:v0.1.0` after `-s --`.

It prints five numbered steps:

| Step | What happens | You do |
|---|---|---|
| 1. Docker and OpenShell | Checks Docker; installs OpenShell if missing | Nothing, unless Docker is not running |
| 2. Image | Pulls it (or uses the one you have) | Wait |
| 3. GitHub token | Asks which repo (default `kyverno/kyverno`), then how to authorize: `gh` sign-in, or a pasted token. Checks it against the repo and stores it in OpenShell, not in the sandbox | Type the repo, then sign in or paste |
| 4. Sandbox | Creates the sandbox and applies the network policy (a minute or two the first time) | Wait |
| 5. Assistant | Reads your GitHub username from the token, then starts the GitHub Copilot sign-in: a page and a code | Open https://github.com/login/device, enter the code, approve, then pick a model |

The chat opens after about 30 seconds; `cpuinfo` and `onnxruntime` lines before it are harmless. Only the first run
asks questions: later, the same command takes you straight back in.

**3. Check it works.** A good first question for the contributor is *"Use find_issues to list 3 open issues I could
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
kyverno --no-openshell --profile contributor
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
container/kyverno --build --profile contributor        # the first build takes 15 to 30 minutes and downloads about 5 GB
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
[README step 1](../README.md#1-create-a-github-token).

**Contributor token:** a fine-grained token of your own account; "Public repositories (read-only)" is enough to read
everything. To comment on an issue, or to open a pull request from your fork, the token must be able to write there
(or use the `gh` sign-in, which can).

Wrong, expired or revoked? A 404 means the token cannot see the repo. Replace it with `--reset`.

## Choosing a model

- **GitHub Copilot** is the default; the sign-in is one click. Which models answer depends on your plan. If the picker's
  choice fails with `The requested model is not supported`, type `/model gpt-4.1 --global` in the chat (`gpt-4.1`
  and `gpt-4o-mini` answered on a free account).
- **A small model works.** The assistant is set up so every tool is visible to the model without searching. If you hit
  a rate limit, it ends in an API error and a retry; wait and try again.
- **Another provider:** `kyverno --provider anthropic` (or `openai`, `nvidia`). The launcher asks for the key once and
  stores it in OpenShell. Then choose a model with `/model`.

## Letting it push to your fork

Off by default. Without it, the assistant commits in the sandbox and hands you a patch to apply and push yourself.
With it, it can run `git push` to branches of **your own forks** (`kyverno`, `website`, `policies`; set
`KYVERNO_PUSH_REPOS` for others) and, as a maintainer, to the repo you manage, and nowhere else:

```bash
KYVERNO_PUSH_REPOS="kyverno" kyverno --profile contributor --push-to-fork
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
`container/kyverno --build --upgrade`.

## Starting over and removing everything

| To | Run |
|---|---|
| Forget the stored GitHub token | `kyverno --reset` |
| Stop the sandbox | `openshell sandbox stop kyverno-contributor` (the launcher starts it again) |
| Delete a sandbox and everything in it | `openshell sandbox delete kyverno-contributor` (maintainer: `kyverno-assistant`) |
| Delete the stored GitHub token | `openshell provider delete kyverno-contributor-github` (maintainer: `kyverno-repo-github`) |
| Delete a model key | `openshell provider delete kyverno-anthropic` (or `-openai`, `-nvidia`) |
| Delete the push token | `openshell provider delete kyverno-git-push` |
| Delete the image | `docker rmi ghcr.io/kyverno/assistant:latest` |

With `--no-openshell`, the clones and toolchains live in a Docker volume that outlives the container:
`docker volume rm kyverno-contributor-docker-workspace` (the name follows the container's).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `docker pull` crawls or stalls | Your route to GitHub's download servers is slow. Check with another network (a phone hotspot); layers already downloaded are kept, so run the pull again |
| `…already exists and keeps running the image it was made from` | The sandbox is from an older image. Add `--upgrade` |
| Started the wrong assistant | It tells you which it chose. Pass `--profile maintainer` or `--profile contributor` |
| `--build needs a checkout of the repository` | You are using the launcher extracted from an image. Use [Case 4](#case-4-building-from-a-clone), or drop `--build` |
| `could not tell whose forks to allow` | An older launcher. Refresh it (see [Upgrading](#upgrading)); or set `KYVERNO_PUSH_LOGIN` to your username |
| The banner says `github … connecting` | Normal for a moment: it connects after the banner is drawn |
| The agent cannot find `find_issues` or the GitHub tools | An older image hides them from the model. Upgrade |
| The agent runs `git clone` and then cannot push | An older image. Upgrade; the assistant should use `kyverno-workspace owner/repo` |
| The dashboard address does not answer | Only the maintainer assistant has a dashboard. If another sandbox left a dead forward, `openshell forward stop 9219` |
| The chat looks frozen after start | Give it 30 seconds, press Enter once, then try `--upgrade` |
| `Docker Desktop … is older than 4.60` | Update Docker Desktop |
| A build is very slow or runs out of memory | Raise Docker's memory (Settings → Resources) to at least 4 GB |
| `sandbox … did not become Ready` | The launcher prints its state and log. Delete the sandbox and run the launcher again |
| `could not register the provider profile` | OpenShell holds an older copy. Delete the sandbox, the provider and `openshell profile delete kyverno-repo-github` (or `-contributor-github`), then run again |

More: the [README troubleshooting table](../README.md#troubleshooting) and [docs/container.md](container.md).

## Checking what you downloaded

Every release image is signed. To verify it came from this repository's release workflow:

```bash
cosign verify ghcr.io/kyverno/assistant:v0.1.0 \
  --certificate-identity https://github.com/kyverno/assistant/.github/workflows/container-image.yml@refs/tags/v0.1.0 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
