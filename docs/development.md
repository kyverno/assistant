# Developing this repository

Repository layout, the Makefile targets, CI and releases.

## Repository layout

```
Makefile                 common lint, image build, scan and run targets (make help)
container/               the sandbox: Dockerfile, launcher, policy, provider profiles (see container/README.md)
.github/workflows/       builds and publishes the sandbox image
profiles/maintainer/     the maintainer profile: distribution.yaml, SOUL.md, config.yaml, hooks/,
                         plugins/, skills/, cron/, sandbox/ (the host install's terminal image and policy)
profiles/contributor/    the contributor profile: same shape, plus one plugin and scripts/contributor-watch.py
container/org/           the organisation pack (org.yaml: everything specific to the organisation), installed
                         into both profiles as org/
scripts/install.sh       the host install: idempotent installer, --profile maintainer|contributor [NAME]
scripts/update.sh        the host install: git pull, then re-run the installer
scripts/openshell.sh     the host install's sandboxed terminal: setup, policy, access requests, audit
scripts/adopt-ci.sh      opens a PR that adds the shared CI actions to other repositories (see above)
.github/actions/         shared CI actions (PR labelling, readiness, stale), usable without the agent
adopt-ci-templates/      workflows and config that adopt-ci.sh copies into each repository
docs/                    design, deployment and usage docs
```

## Development automation

Run `make help` to see the available commands. The most useful targets are:

| Command | Purpose |
|---|---|
| `make lint` | Run ShellCheck and Hadolint using the same pinned images as CI |
| `make image` | Build `assistant:dev` locally |
| `make test` | Build the image and run its offline startup, plugin and memory checks |
| `make scan` | Scan the local image for fixable critical vulnerabilities |
| `make check` | Run lint and image-build checks |
| `make run ARGS="--provider anthropic"` | Launch the sandbox using the locally built image |

Override `IMAGE` or `TAG` for a different local image name, for example `make image TAG=debug`.

The files below are the maintainer profile's, under `profiles/maintainer/`.

<details>
<summary><b>Details</b></summary>

**Core files**

- `config.yaml`: the model default, the GitHub and Slack MCP server declarations (`mcp_servers:`),
  and the hand-picked tool allowlist. Security-critical, so edit by hand only.
- `container/Dockerfile` and `container/setup-profile.sh`: build the sandbox image and do the one-time profile setup
  at build time.
- `scripts/install.sh`: the host install: profile install, credential check, gateway install, AGENTS.md cache
  seeding, toolset sanity checks.
- `cron/jobs.json`: a weekday review digest posted to Slack and two silent memory jobs
  (`memory-sweep`, `memory-consolidate`). All install paused.

**Hooks**

- `profiles/maintainer/hooks/block-dangerous-tools.sh`: rejects any merge, delete-repo or force-push-shaped tool call.
- `profiles/maintainer/hooks/block-mnemosyne-triples.sh`: restricts the memory plugin's `triple_add` to the exact facts
  this distribution writes.

**Plugins**

- `profiles/maintainer/plugins/repo-intel/`: the fetch tools (`fetch_pr_candidates`, `fetch_file_diff_overlap` and the
  issue equivalents, one batched call over GitHub's GraphQL and REST APIs) `sequence_prs` (a hard-dependency graph
  with cycle detection and topological layering into tiers), and `repo_pack` (the organisation pack's rules and facts).
- `profiles/maintainer/plugins/maintainer-dashboard/`: the `update_dashboard` tool and the Maintainer tab in
  `hermes dashboard`.

**Skills**

| Skill | Purpose |
|---|---|
| `repo-context` | Live labels, CODEOWNERS and milestone lookup, plus the organisation pack's gates, file classes and facts (Kyverno's are in `container/org/kyverno/`) |
| `pr-queue` | Merge-sequence recommendations and per-PR review briefs |
| `pr-actions` | Labels, comments, reviews and branch updates via your token |
| `dependabot-queue` | Dependabot PR verdicts |
| `issue-triage` | Issue classification, issue and PR relationships, progress, staleness |
| `issue-actions` | Issue label, close, assign, milestone, nudge and raise |
| `author-followup` | Stale and author-blocked PR decisions |
| `discussions` | Reads and answers GitHub Discussions, with confirmation before posting |
| `dashboard-sync` | Pushes presented results and confirmed actions to the dashboard |

</details>

## Further reading

| Doc | Contents |
|---|---|
| [`docs/getting-started.md`](getting-started.md) | Setup for every case: Apple Silicon, Linux, Intel or Docker only, a clone; tokens, models, pushing to your fork, upgrading, removing, troubleshooting |
| [`docs/container.md`](container.md) | The sandbox in depth: what runs where, secrets, the network policy, what was verified, upgrades, a security review, how to update dependencies, publishing a release |
| [`docs/host-install.md`](host-install.md) | Running without the sandbox: install, Slack setup, the review digest |
| [`docs/capabilities.md`](capabilities.md) | A tour of what it can do, with example prompts |
| [`docs/deployment.md`](deployment.md) | Install runbook and every environment variable (host install) |
| [`docs/architecture.md`](architecture.md) | Design and security model |
| [`docs/adopting-actions.md`](adopting-actions.md) | Adding the shared CI actions to a repository and turning them on |
| [`docs/generalisation.md`](generalisation.md) | How the CI actions and the assistants become adoptable by any organisation |
| [`docs/workflow.md`](workflow.md) | The target maintainer workflow and build plan |
