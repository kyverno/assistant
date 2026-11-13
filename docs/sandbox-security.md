# How the sandbox protects you

The full detail behind the [README](../README.md#protected-by-openshell): what the agent cannot see, what it can reach, every
OpenShell policy, what happens when it is tricked, and the limits. For what was tested and how, see
[`container.md`](container.md); for the design, [`openshell-architecture.md`](openshell-architecture.md).

An assistant that reads strangers' issues and PRs will, sooner or later, read text written to manipulate it, and it also
runs on a machine that holds things that are not yours to share. The design answer to both is the same: **the agent runs in
a sandbox where a tricked agent still has nothing to steal and nowhere to send it.** [OpenShell](https://github.com/NVIDIA/OpenShell)
provides that, and its strongest limits sit **outside the sandbox, where the agent cannot edit them.**

## What the agent cannot see or reach

| Not available to it | Why |
|---|---|
| **Your files**: home folder, documents, downloads, your other projects and repositories, your employer's code | The sandbox is a separate container with its own filesystem. Nothing from your computer is mounted into it |
| **Your credentials**: SSH keys, cloud credentials, password manager, browser sessions | They are not in the sandbox, so there is nothing to read |
| **Your GitHub token** | OpenShell holds it. The sandbox holds only a placeholder, and the real token is swapped in on the way out to GitHub |
| **Your network**: the office network, VPN-only services, anything on localhost | The network is deny-by-default: the sandbox reaches only the hosts on an allowlist |
| **Other repositories**, if you choose | Use a fine-grained token scoped to one repository, and an organisation can require it ([below](../README.md#for-organisations-publish-an-assistant-policy)) |

What it *can* reach, and only these: GitHub's read APIs and the GitHub MCP server (a fixed list of tools), the model host
(GitHub Copilot, or the provider you chose), read-only GitHub for cloning in its terminal, the language-toolchain download hosts
(GET only, every download checked against the vendor's checksum), and a read-only model list. Plus one forwarded port on your
own loopback for the dashboard. Nothing else.

## The policies OpenShell enforces

| Policy | What it does |
|---|---|
| **Filesystem** (Landlock, a hard requirement) | Read-only: the system, Hermes' code and the baked profile (the safety hooks and the organisation pack). Writable: `/sandbox` (the clones and work), `/tmp`, and Hermes' home (memory and state). Fixed when the sandbox is created |
| **Network**, deny by default | Named allow-blocks, each limited by host, port, path, HTTP method **and the program allowed to call**: read-only GitHub for `git` and `curl`; toolchain hosts, GET only, for `curl`, Go and Node; the two Copilot login paths for Hermes' own Python; the model's inference paths by name; a read-only model catalog. It can be reloaded on a live sandbox |
| **Your GitHub token** | Kept in OpenShell, swapped in only for the listed endpoints and the listed programs. REST is read-only, and GraphQL mutations (including `mergePullRequest`) are refused by the proxy |
| **GitHub MCP tools**, checked per call | The proxy allows only the tools on the list and refuses everything else, plus anything named like `*merge*`, `*delete*` or `*force_push*`, even hidden in a batched request. The list must equal the one in the profile or the image build fails |
| **Asking, not widening** | If a task needs a blocked host, the agent can *propose* the narrowest rule. You approve it on your own computer with `openshell rule approve`; it cannot approve it itself |
| **A full audit trail** | `openshell logs assistant-maint` (or `assistant-contrib`) shows every request the sandbox made and whether it was allowed or denied |
| **Pushes only if you opt in** | `--push-to-fork` adds a separate credential for your own fork. Only `git` can use it, only for your fork, and a hook still refuses force pushes, deletes and pushes to main or release branches |
| **Your organisation's policy** | The launcher enforces which repositories the assistant may be started on and which token types it accepts ([below](../README.md#for-organisations-publish-an-assistant-policy)) |

On top of that sit safety hooks, defence in depth: one blocks any tool whose name looks like merge, delete or force-push,
one guards `git push`, one keeps the terminal inside the sandbox. They run from the read-only copy, so the agent cannot edit
them. There is no merge tool at all, and merging stays your decision.

## If the agent is tricked

Text in an issue says "ignore your instructions and...". What happens:

| It is told to | What stops it |
|---|---|
| Read your files, SSH keys or other projects | They are not in the sandbox |
| Print or leak your GitHub token | It holds only a placeholder |
| Send data to a host it picked, or reach your office network | The network is deny-by-default: the request is blocked and logged |
| Reach GitHub with the token through another program | The token is only swapped in for the listed programs |
| Merge a PR, delete the repository, force-push | No such tool exists; the proxy refuses the tool names and GraphQL mutations; the token has no merge permission |
| Rewrite its own safety hooks | They are read-only to it |

## Honest limits

Layers that live inside the sandbox (the tool list in `config.yaml`, the hook that blocks merge-like names) can be edited by
a manipulated agent, because it has a terminal and file tools. The ones that matter (the token, the proxy, the network
allowlist, the read-only files) do not depend on anything it can write. The one credential inside the sandbox is the Copilot
sign-in token, which Hermes needs to call the model; the network allowlist is what stops it going anywhere else. The sandbox
limits what the agent can reach, not whether what it *says* is right, so check the links it cites before you act. What was
tested, and how: [`container.md`](container.md); the design: [`openshell-architecture.md`](openshell-architecture.md),
[`openshell.md`](openshell.md) and [`SECURITY.md`](../SECURITY.md). Without OpenShell (`assistant --no-openshell`, for an
Intel Mac or Docker only) the protection is much weaker, and the docs say exactly how.
