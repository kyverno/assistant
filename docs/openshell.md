# Sandboxed terminal (OpenShell)

`./scripts/install.sh` offers to give the assistant a terminal that runs inside an
[NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandbox. Without it the profile has no terminal.
Hermes stays on your machine; its terminal and file tools (`read_file`, `write_file`, `patch`,
`search_files`) connect to the sandbox over ssh, so you keep working in the same Hermes session.
The sandbox cannot see your own files; to work on a repository the assistant clones it into `/sandbox`.

## What the sandbox allows

Everything is in [`profiles/maintainer/sandbox/policy.yaml`](../policies/terminal.yaml). Anything not listed there is denied.

| Area | Rule |
|---|---|
| Files | System paths read-only; `/sandbox` and `/tmp` writable; enforced by the kernel (Landlock) |
| Network | Denied by default. Read-only GitHub: `api.github.com`, `github.com` (clone and fetch, no push), `codeload.github.com`, `raw.githubusercontent.com` |
| Programs | Only `curl` and `git` may reach those hosts; `python3` and everything else is blocked |
| Tokens | None. The sandbox has no GitHub, Slack or model credentials |

`profiles/maintainer/hooks/require-sandbox-terminal.sh` blocks every terminal and file-tool call unless the profile's terminal backend is
ssh into an `openshell-*` host, so a changed config cannot move the terminal onto your machine.

## Changing the policy

Edit `profiles/maintainer/sandbox/policy.yaml`, then:

```bash
./scripts/openshell.sh policy      # network rules apply to the running sandbox
```

File-system rules are fixed when the sandbox is created: `./scripts/openshell.sh remove` then
`./scripts/install.sh`. To allow another package registry or API, add a `network_policies` entry with
the host, the allowed methods and paths, and the programs that may use it.

## Access requests

When a command is blocked and the task needs it, the agent (guided by the `sandbox-access` skill) can
ask for a narrow permission: an exact host, specific methods and a path, for one program. Nothing is
approved automatically; only you approve, from your own terminal:

```bash
./scripts/openshell.sh rules list                  # pending requests: host, methods, program, reason, risk check
./scripts/openshell.sh rules approve <id>          # the rule goes live in the running sandbox
./scripts/openshell.sh rules reject <id> [reason]  # the agent sees the reason
./scripts/openshell.sh rules history
```

OpenShell marks approved agent rules as audit-only unless enforcement is set, which would let any
path and method through; `rules approve` switches enforcement on for every approved REST rule. A
request that opens a whole host (no methods or paths) is refused unless you add `--allow-whole-host`.
Approved rules last until the policy is re-applied (`scripts/openshell.sh policy`); add the ones you
want to keep to `profiles/maintainer/sandbox/policy.yaml`.

## Audit trail

Every allowed and denied connection, request, approval and rejection is recorded by OpenShell with the program, host, method, path,
the policy that decided and the reason.

```bash
./scripts/openshell.sh audit summary [YYYY-MM-DD]   # denied requests, findings, policy changes
./scripts/openshell.sh audit tail                   # live stream
```

OpenShell keeps only the latest 2,000 lines per sandbox in memory. A background job (launchd, every
five minutes) copies new lines to `~/.kyverno-assistant/audit/YYYY-MM-DD.log`, leaving out
OpenShell's internal status notices, and deletes daily logs older than 20 days. A day's log is a few
kilobytes.

## Other commands

```bash
./scripts/openshell.sh status    # is the terminal usable?
./scripts/openshell.sh remove    # delete the sandbox, ssh entry and audit job (logs are kept)
```

## Requirements

Apple Silicon macOS with Docker Desktop 4.60 or newer (needs Landlock), Docker running. The installer
installs OpenShell v0.1.3 with Homebrew and, on Docker Desktop, adds one line to the OpenShell
gateway config so sandboxes can reach it (backup saved next to the file). Other platforms keep the
terminal off.
