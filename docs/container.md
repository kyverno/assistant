# Running the assistant in an OpenShell sandbox

The assistant (Hermes, its Kyverno profile, and the memory plugin) runs entirely inside one
[OpenShell](https://github.com/NVIDIA/OpenShell) sandbox, started from one image. You install Docker; the
launcher does the rest.

```bash
container/kyverno --build     # first time: builds the image from this checkout, then starts it
container/kyverno             # every later run: reattach (or start) the sandbox
container/kyverno --build --upgrade   # after pulling a new version: rebuild and recreate, keeping your state
```

Needs Docker Desktop 4.60 or newer on Apple Silicon macOS, or Docker on Linux (x86_64 or arm64; what has been tried on
each is in the table under "What works").
The first image build takes 15 to 30 minutes on a laptop with 8 GB of RAM; after that, starting is quick.

## What runs where

| Where | What runs there |
|---|---|
| **Your laptop** | Docker, the OpenShell CLI and gateway, and the launcher (`container/kyverno`), plus one forwarded port (`127.0.0.1:9219`) to the dashboard. Nothing else. |
| **OpenShell gateway** (on the laptop) | Holds your GitHub token and any model API keys, enforces the network policy through a proxy, and keeps the audit log. |
| **The sandbox** | Hermes, the profile (skills, plugins, hooks), the memory plugin and its database, the dashboard, the agent's terminal, and the Copilot sign-in token. |

The image is built once. Hermes is pinned to a commit and every package it needs is installed at build time,
because the sandbox has no open internet and the chat screen hides first-use prompts.

## How secrets are handled

| Secret | Where it lives | What the sandbox sees |
|---|---|---|
| GitHub token (fine-grained PAT, or the `gh` sign-in's token) | OpenShell, asked once by the launcher | A placeholder in `REPO_GITHUB_TOKEN`. The proxy swaps in the real value on requests to `api.github.com` (reads) and the GitHub MCP server, and only from Hermes' Python. |
| Model API key (`--provider anthropic\|openai\|nvidia`) | OpenShell, asked once | A placeholder. Hermes accepts it for these providers. OpenRouter is not supported this way: Hermes requires an `sk-or-` prefix. |
| GitHub Copilot sign-in | Created by Hermes' own device-code login inside the sandbox | The real token, in the profile's `.env`. It only grants Copilot model access. |

| Push token (`--push-to-fork`, optional) | OpenShell, asked once, apart from the read-only repo token | A placeholder in `GIT_PUSH_TOKEN`. The proxy swaps in the real value on `git push` to the allowed repositories only, and only from `git`. |

The Copilot token and the repo token never share a variable name, so Hermes cannot pick the wrong one.

## What the agent can reach

Everything not listed is denied (`policy.yaml` and `providers/*.yaml`).

| Destination | Allowed |
|---|---|
| `api.github.com` REST and GraphQL | Reads only. Merges, repo deletion and GraphQL mutations are refused by the proxy. |
| `api.githubcopilot.com/mcp/` (GitHub MCP) | Only the tools on the allowlist (the same list as `X-MCP-Tools` in `config.yaml`), with the repo token swapped in. Anything else, and anything named like merge, delete or force-push, is refused by the proxy |
| Copilot inference (`api.githubcopilot.com`: `/models`, `/chat/completions`, `/responses`, `/embeddings`, `/v1/**`; and `api.individual`, `api.business`, `api.enterprise.githubcopilot.com`) | Yes, with Hermes' own Copilot token. `/mcp/` is deliberately not among the paths on the shared host (see "Safety layers") |
| `github.com` login endpoints | Only the two device-code paths |
| `github.com`, `codeload`, `raw.githubusercontent.com` | Read-only, for the agent's terminal, no credential |
| Toolchain hosts (`go.dev`, `proxy.golang.org`, `nodejs.org`, `registry.npmjs.org`, and a few more) | GET only, and only for the installer's own binaries (`curl`, Go, Node). Used by `kyverno-workspace`; every download is checked against the vendor's checksum |
| `github.com` `git-receive-pack`, only with `--push-to-fork` | Pushes to your own forks (and, for a maintainer, the repository they manage), nothing else. The credential is swapped in by the proxy; a hook also refuses force pushes, deletes and pushes to main |
| `models.dev` | Read-only, for the `/model` list |
| A model provider you added with `--provider` | Only that provider's API host |

## What works

Verified means it was run in a real sandbox, on the image as built from this checkout.

| Area | Status | Notes |
|---|---|---|
| Image build, sandbox start, first run | Verified | First run takes about a second; agent start to reply about 30 seconds |
| GitHub MCP with the real token | Verified | Connects, 63 tools. Merge tools are not in the list |
| GitHub MCP tool allowlist enforced by the proxy | Verified | With a real token: `get_me` is answered; `merge_pull_request` is refused by the name deny rule with `policy_denied` before reaching GitHub, and `create_pull_request` is admitted (it was refused until it was added to the allowlist); a batch with a merge hidden among allowed calls is refused too. `hermes mcp test github` connects through the rules. Closing an MCP session with `DELETE` is rejected by the proxy (it carries no JSON body), which only means the server closes the session itself later |
| REST and GraphQL reads with the real token | Verified | Merge, delete and GraphQL mutations refused by the proxy |
| Safety hooks (merge blocker, mnemosyne triples, terminal guard) | Verified | Approved at build time, listed as allowed |
| Memory plugin (mnemosyne) | Verified | Starts, searches, consolidates, with the network off. Embedding model is in the image |
| Skills, plugins (`kyverno-fetch`, `kyverno-sequencer`) | Verified loaded | 63 GitHub tools, 29 skills in the banner |
| GitHub Copilot sign-in and a model reply | Verified | Device-code login works inside the sandbox. Which models answer depends on your plan: see below |
| Anthropic / NVIDIA / OpenAI keys | Partly | The request reaches the provider with the key swapped in. A real reply is untested |
| Agent terminal and file tools | Enabled, not exercised | They run locally in the sandbox; the hook allows them only inside OpenShell |
| Slack | Not available | The Slack MCP server runs under Docker, which a sandbox does not have |
| Linux (amd64) | Verified on a runner | On a GitHub runner (Ubuntu 24.04, kernel 6.17, Docker Engine 28): an amd64 image was built there and the whole stack ran, with the placeholder token, the proxy refusing a merge, read-only hooks and the dashboard answering. Not a real laptop, and not by the publish workflow; arm64 Linux is untried |
| Dashboard | Verified | Runs in the sandbox; the launcher forwards `127.0.0.1:9219`. A sample item written through the agent's own `update_dashboard` function was served by it, with the Kyverno tab's script and styles loading |
| Scheduled jobs (cron, the Slack digest) | Not available | They run inside the Hermes gateway, which is not started |
| `AGENTS.md` cache seeding | Not done | `kyverno-context` falls back to searching GitHub |
| Keeping state when the image is upgraded | Verified | `--upgrade`: see below |

## Upgrading and your state

State lives inside the sandbox and survives stopping and starting it. A new image means a new sandbox, so
`container/kyverno --build --upgrade` (or `--image NEW`) saves your state, recreates the sandbox from the new
image, and puts the state back. It carries only what is yours:

- the memory database, the notes in `memories/`, the chat history and sessions, and the dashboard's data
- your repo and username, and the Copilot sign-in (just those settings, not the whole `.env`)
- the model you chose
- your cloned repositories under `/sandbox`, with their unpushed commits and edits. The downloaded toolchains and build caches
  (`/sandbox/.toolchains`, large) only come along with `--keep-caches`; otherwise they download again

The profile itself (skills, plugins, hooks, settings) always comes from the new image. If saving fails, the old
sandbox is left alone. If restoring fails, the saved copy stays in a temporary folder and its path is printed; it
holds your Copilot token, so delete it when you are done. OpenShell only moves files in and out of `/sandbox`,
so the bundle goes through there and is removed afterwards. Skills you created inside the sandbox are not carried.

## Safety layers

The agent cannot merge, and no single mechanism is trusted on its own:

1. The GitHub token has no merge permission (Pull requests: write without Contents: write).
2. The proxy, outside the sandbox, checks every GitHub call. REST and GraphQL on `api.github.com` are reads only:
   merges, repo deletion and GraphQL mutations are refused. On the GitHub MCP server, each `tools/call` must name a
   tool on the allowlist in `providers/repo-github.yaml` and must not match `*merge*`, `*delete*` or `*force_push*`;
   one refused call refuses a whole batched request.
3. The GitHub MCP tool allowlist (`X-MCP-Tools` and `tools.include` in `config.yaml`) has no merge tool.
4. A pre-tool hook blocks tools whose names look like merge, delete or force-push.
5. The network policy denies everything not listed above.

Layers 1, 2 and 5 are not files the agent can reach: the token lives in OpenShell, and the policy and provider
profile are applied by the launcher through the gateway. Layers 3 and 4 live in `config.yaml` and the hooks. The
hook *scripts* run from `/opt/kyverno/profiles/maintainer/hooks`, which is root-owned and read-only under the sandbox
policy. `config.yaml` is writable by the sandbox user, and the agent has a terminal and file tools, so a manipulated
agent could change it, for example to remove a hook entry or widen the tool list. Layers 1, 2 and 5 are what hold then:
the proxy's tool allowlist, the token's scopes and the network rules live outside the sandbox.

**Why the Copilot paths in `policy.yaml` are listed by name.** `api.githubcopilot.com` serves both Copilot inference
and the GitHub MCP server (`/mcp/`). OpenShell admits a request if any rule for the host admits it, and a deny rule on
one endpoint applies to the whole host. So an "allow every path" rule for inference would also admit every MCP tool
call, merges included, and make layer 2's tool check meaningless. The inference rules therefore name the paths Hermes
uses. If Copilot ever needs another path, add it there; never `/**`. This was found by testing a sandbox with the
earlier all-paths rule: `merge_pull_request` reached GitHub. After the change it is refused.

**Keeping the two tool lists equal.** The allowlist exists twice, in `config.yaml` and in
`providers/repo-github.yaml`. The image build compares them and fails if they differ, so a tool added to one cannot
silently be missing from the other.

## Running without OpenShell

OpenShell is the default and the way to run this. If you do not want it, `container/kyverno --no-openshell` runs the
same image as one plain Docker container instead. It is an opt-in with weaker protection, not an equal alternative.

```bash
container/kyverno --no-openshell [--build | --image IMAGE] [--provider P ...] [--upgrade] [--reset] [--allow-terminal]
```

| | OpenShell (default) | `--no-openshell` |
|---|---|---|
| GitHub token and model keys | Held by the gateway. The agent sees placeholders | In the agent's environment, as real values |
| Network | Allowlist, enforced by the proxy | Whatever Docker allows: the whole internet |
| Merges | Refused by the proxy, whatever the token can do | Only the token's scopes stop them. Use a fine-grained token (README step 1), not the `gh` sign-in |
| Tool allowlist on the GitHub MCP server | Enforced by the proxy | Hermes' own filter only |
| Terminal and file tools | Allowed, inside the sandbox | Blocked. `--allow-terminal` turns them on, and then the container is the only boundary |
| Coding environment (`kyverno-workspace`) | Downloads only from the allowed toolchain hosts | The same scripts and checksums, but the container can reach anything, so the host allowlist is not there. Needs `--allow-terminal` |
| Pushing (`--push-to-fork`) | Token held by the gateway, pushes limited to your forks | Not available: the launcher refuses it. The assistant hands over patches |
| Audit log | `openshell logs` | None |
| Dashboard | Forwarded to `127.0.0.1:9219` | The same address. Hermes keeps it on loopback inside the container; a small relay passes it to a port Docker publishes on `127.0.0.1` only, on a network that only this container joins |

What it still does: the container runs as a non-root user with every Linux capability dropped, `no-new-privileges`
and a process limit, and nothing from your machine is mounted. Docker is the only requirement. It was tried on Apple Silicon only.

**Keys.** They live in `~/.config/kyverno-assistant/secrets.env` (override with `KYVERNO_SECRETS_FILE`), mode 600, as
`KEY=value` lines. On every start the launcher copies the allowed keys (`REPO_GITHUB_TOKEN`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `NVIDIA_API_KEY`) into a tmpfs inside the container. They are not in the image, not in
`docker inspect`, and not on the container's disk; the tmpfs is empty again when the container stops.

**State.** It lives in the container, so it survives `docker stop` and `docker start`. `--upgrade` saves it, recreates the
container from the new image and restores it, as with OpenShell. Removing the container by hand (`docker rm`) loses it.

**Keys changed?** Re-running the launcher copies the new keys in and restarts the dashboard so it picks them up.

**Removing it.** `docker rm -f kyverno-assistant-docker`, `docker network rm kyverno-assistant-docker-net`, and delete the secrets file.
This loses the container's state.

**Choosing between them later.** The two modes use different names (`kyverno-assistant` sandbox, `kyverno-assistant-docker`
container), so they do not interfere. State is not shared between them.

## Choosing a model

- **GitHub Copilot** is the default. A free account may not allow the model Hermes pre-selects. Copilot's
  model list marks several models `enabled` that a free account still rejects with
  `model_not_supported`, so only a real request tells you. `gpt-4.1` and `gpt-4o-mini` answered on a free
  account. Switch in chat with `/model gpt-4.1 --global`.
- **Another provider:** `container/kyverno --provider anthropic` (or `openai`, `nvidia`). The key is
  asked once and kept in OpenShell. The proxy adds the real key only on chat and model-list requests
  (`/v1/chat/completions`, `/v1/responses`, `/v1/messages`, `/v1/models`). Anything else on those hosts is refused,
  so a prompt-injected command cannot use your key for file uploads, batch jobs or fine-tuning. Hermes features
  that use other endpoints with the same key (OpenAI speech, transcription or image generation) do not work
  until the path is added to `container/providers/<provider>.yaml`. `make lint-providers` fails if a provider
  profile allows every path again.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Chat looks frozen right after start | Hermes may be waiting on a question the screen hides. This image is built so that does not happen; if it does, rebuild with `--build` |
| `The requested model is not supported` | The model is not available on your Copilot plan. `/model gpt-4.1` |
| `No LLM provider configured ... openrouter` | You selected a mixture-of-agents option that needs an OpenRouter key. Pick a normal model |
| Sandbox goes to `Error`, "UID or GID zero" | The image's default user must be 1000, not root |
| Agent takes about 30 seconds to appear | It loads the memory plugin's search engine. The `cpuinfo` and `onnxruntime` lines are harmless |
| Image build is slow or fails to pull | Docker Desktop's VM memory. 8 GB hosts leave Docker about 4 GB |
| Needs the real reason a sandbox is not Ready | The launcher prints the sandbox state and the last lines of its create log |

## Publishing the image (maintainers)

Users can always build the image themselves with `container/kyverno --build`. To let them download it instead
(a few minutes, and identical for everyone), `.github/workflows/container-image.yml` builds it in CI and publishes
it to the GitHub Container Registry as `ghcr.io/<owner>/assistant`, one multi-architecture image for Apple
Silicon / arm64 Linux and Intel / amd64 Linux. Each architecture is built on a native runner.

To release: push a semantic-version tag.

```bash
git tag -s v0.1.0 && git push origin v0.1.0   # publishes v0.1.0, and moves v0.1, v0 and "latest"
```

| Tag | Means | Moves when |
|---|---|---|
| `v0.1.0` | One exact release; never changes | never |
| `v0.1` | The newest patch of that minor version | a stable `v0.1.x` is released |
| `v0` | The newest minor of that major version | a stable `v0.x.y` is released |
| `latest` | The newest stable release | any stable release |
| `v0.2.0-rc1` | A pre-release (any tag with a suffix) | never; it does not move the tags above |
| `edge` | A manual run from the Actions tab | each manual run |

Only tags of the form `vMAJOR.MINOR.PATCH` (optionally with a suffix) start a release. Below `v1.0.0` the project
is still early, so a minor version may change behaviour.

One-time setup for an org admin:

1. Allow the repository's workflows to publish packages (the workflow asks for `packages: write`).
2. After the first publish, open the package's settings and make it **public**. New packages are private, and the
   launcher pulls without logging in.

The launcher pulls `ghcr.io/kyverno/assistant:latest` by default. To pin a version, or to use a fork or a different
registry, set `KYVERNO_IMAGE` or pass `--image`:

```bash
container/kyverno --image ghcr.io/kyverno/assistant:v0.1.0 --upgrade     # a release
container/kyverno --image ghcr.io/kyverno/assistant@sha256:<digest>       # exactly this build
```

Before the manifest is created, the workflow scans both architectures with Trivy and stops if either has a fixable
CRITICAL vulnerability, so a bad image never gets the release tag or `latest`.

Each published image is signed with cosign (keyless: the signature is tied to this workflow's identity, with no key
to manage) and has a build provenance attestation. To check an image before running it:

```bash
cosign verify ghcr.io/kyverno/assistant:v0.1.0 \
  --certificate-identity-regexp '^https://github.com/kyverno/assistant/\.github/workflows/container-image\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

gh attestation verify oci://ghcr.io/kyverno/assistant:v0.1.0 --owner kyverno
```

**Not tested yet:** the workflow has run by hand (the `edge` tag, both architectures, with the scan gate), but a tag
push has not: the release tags, the floating tags, signing and the provenance attestation have never run. They
were checked with actionlint only. Cut the first release as `v0.1.0` and run both verify commands above.

## Security notes

Checked when this was written: shellcheck on every script, hadolint on the Dockerfile, a secret scan of the files,
the image's contents, and a vulnerability scan with Trivy. The scan numbers change from day to day, so they are not
recorded here; the command to rerun it is under "Updating dependencies".

**In place:**

- The base image is pinned by digest, and OpenShell's installer is fetched from its pinned tag, not `main`.
- A failed download inside a piped build step fails the build.
- The repo and username you type are validated before they are used in a URL or written to a settings file.
- The safety hooks run from a read-only location (`/opt/kyverno`), and the build checks that the agent cannot write
  to them or to Hermes' code.
- The final image contains only live files (see "How the image is built, and why").
- The GitHub MCP tool allowlist is enforced by the proxy as well as by `config.yaml`, and the build checks they match.
- The one CRITICAL the scan found (PyJWT, an authentication bypass) is patched in the image, and the publish workflow
  refuses to release an image with a fixable CRITICAL.

**Known limits, not fixed:**

- The agent can edit its own `config.yaml` inside the sandbox (above).
- Hermes' installer is fetched from Hermes' website while the image is built (`curl | bash`). That is the same trust as
  running Hermes at all, and the Hermes commit it installs is pinned, but the script itself is not.
- Hermes' own pinned dependency set has known vulnerabilities below CRITICAL in the Trivy scan (Python and Node
  libraries, none in OS packages). They come from the Hermes version we pin, so the fix is to bump it. Whether each one
  is reachable in this setup has not been assessed. PyJWT is the exception: Hermes pins 2.13.0 and the image overrides
  it with 2.14.0, which Hermes itself does not declare support for; the build's checks pass with it.
- Packages the memory plugin brings in (`fastembed`, `onnxruntime`, ...) and `apt` packages are resolved when the
  image is built, not locked with hashes. `mnemosyne-hermes`, `mnemosyne-memory` and `anthropic` are pinned by hand.
- The published image is not signed and has no SBOM or provenance attestation.
- The launcher installs OpenShell with its own installer (`curl | sh`, from a pinned tag, but not checksum-verified).
  OpenShell is 0.x and the launcher reads its command output; it was tested with 0.1.3.
- There is no automated end-to-end test of the launcher and sandbox. The image build proves the memory plugin loads
  and works offline, CI lints the scripts, and a full chat is only tested by hand.

## Updating dependencies

Things are pinned so builds are reproducible, which means updates are deliberate. What moves what:

| Dependency | Pinned in | How it is updated |
|---|---|---|
| Base image | Both `FROM ...@sha256:` lines in `container/Dockerfile` | Dependabot proposes a new digest weekly. Build, then scan it (below) |
| GitHub Actions | Commit hashes in `.github/workflows/` | Dependabot, weekly |
| Hermes | `HERMES_COMMIT` in the Dockerfile | By hand. See below |
| `mnemosyne-hermes`, `mnemosyne-memory`, `anthropic`, `PyJWT` | The `uv pip install` line in the Dockerfile | By hand, when Hermes moves. Drop the PyJWT pin once Hermes requires 2.14.0 or newer |
| OpenShell | `OPENSHELL_VERSION` in `container/kyverno` | By hand. See below |
| shellcheck, hadolint | Digests in `.github/workflows/container-lint.yml` | By hand, occasionally |
| Trivy (the release gate) | Digest in `.github/workflows/container-image.yml` | By hand, occasionally |

**Bumping Hermes** (about monthly, and whenever a scan shows a CRITICAL):

1. Change `HERMES_COMMIT` in the Dockerfile and build. The build fails if the memory plugin does not load or needs the
   network, if Hermes cannot tell its own version, or if the agent could write to Hermes' code or the safety hooks.
2. Check whether the `mnemosyne-*` pin is still right: remove it, build, and see if the memory provider still
   loads. It was needed because `mnemosyne-hermes` 0.7.4 refuses to start with the Hermes we pin.
3. Start a sandbox from the new image and check: the hooks are listed as allowed (`hermes -p kyverno hooks list`),
   the GitHub MCP connects (`hermes -p kyverno mcp test github`), and a short chat answers.
4. Scan the new image:
   ```bash
   docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image \
     --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed kyverno-assistant:dev
   ```

**Bumping OpenShell:** change `OPENSHELL_VERSION`, then run the launcher through: first start, `--upgrade`,
`--provider`, and a policy re-apply. The launcher parses `openshell sandbox list` and `openshell provider list`,
and the provider profile format and policy schema are OpenShell's, so those are what can break.

**Not rebuilt, but can change under you:** GitHub's and Copilot's APIs, which Copilot models your plan allows, and
Docker Desktop. The first two only need the chat to be tried; the launcher checks Docker Desktop is 4.60 or newer.

## How the image is built, and why

Anything Hermes would fetch, install or ask on first use is done when the image is built:

- the profile, skills, hook paths and terminal settings (`container/setup-profile.sh`)
- the mnemosyne memory plugin, pinned to `mnemosyne-hermes 0.7.3` and `mnemosyne-memory 4.0.0b3` (0.7.4
  stops the memory provider from starting with this Hermes)
- the `anthropic` library (and a patched PyJWT), and the embedding model the memory plugin reads from the profile cache
- approval of the profile's own hooks (`hooks_auto_accept`)
- removal of what the finished image does not need: the Python environments that Hermes' installer and plugin installs
  leave behind (about 0.9 GB; Hermes' own `hermes pm gc` treats them as still in use) and its package download cache.
  They are only history for an in-place repair, which needs the network. The Dockerfile has two stages, so the final
  image is assembled from live files only and none of that is left in a layer

The build fails if the memory provider does not load, if the settings it applied did not land (hook paths, Slack removal,
auto-approved hooks, terminal backend and tools), if the proxy's tool allowlist differs from `config.yaml`, and its last step runs a full memory cycle (import,
search, consolidate) with no network at all, so a missing download shows up at build time, not on a laptop.
