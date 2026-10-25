# Running the assistant in an OpenShell sandbox

Everything the assistant needs (Hermes, pinned, the maintainer profile and its memory plugin) is in one image
that runs inside an [OpenShell](https://github.com/NVIDIA/OpenShell) sandbox. You install nothing but Docker.

```bash
container/kyverno --build                  # first time: build the image from this checkout, then start
container/kyverno                          # later: reattach to (or start) the sandbox
container/kyverno --provider anthropic     # also use another model provider (anthropic, openai, nvidia)
container/kyverno --build --upgrade        # new version: recreate the sandbox, keeping your state
```

What runs where, how secrets are kept, what the agent can reach, what works today and what doesn't, and
troubleshooting are all in **[docs/container.md](../docs/container.md)**.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | The image: Hermes pinned by commit, the profile baked in, plus build-time proofs that it works |
| `setup-profile.sh` | One-time profile setup, run while the image is built |
| `kyverno-start` | Runs inside the sandbox: your repo and username, the Copilot sign-in, then the chat |
| `kyverno-main` | The sandbox's main process: runs the dashboard in the background and keeps the sandbox alive |
| `kyverno` | The launcher you run on your laptop |
| `lib.sh` | Shared by both launchers: output helpers, the GitHub token prompt, the image build or pull, and the list of state kept across `--upgrade` |
| `kyverno-env`, `dashboard-relay` | Used only by `--no-openshell`, inside the container: reads the keys the launcher copied in, and passes the dashboard to the port Docker publishes |
| `kyverno-docker` | What `kyverno --no-openshell` runs: the same image in plain Docker, without OpenShell (less protection; see docs/container.md, "Running without OpenShell") |
| `restore-state.sh` | Uploaded by the launcher during `--upgrade` to put your state back |
| `policy.yaml` | The sandbox's filesystem and network policy. Keep the Copilot inference paths listed by name (see docs/container.md, "Safety layers") |
| `providers/*.yaml` | OpenShell provider profiles: the GitHub token (with the MCP tool allowlist the proxy enforces) and the model-provider keys (each limited to chat and model-list paths) |
| `hooks/require-sandbox-terminal.sh` | Lets the terminal run locally, but only inside an OpenShell sandbox (or a plain-Docker container started with `--no-openshell --allow-terminal`) |

## Not done yet

Slack and scheduled jobs are not available inside the sandbox yet. The published multi-architecture image is
available from `ghcr.io/kyverno/assistant`; use `container/kyverno --build` to build it from a local checkout.
