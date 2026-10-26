# The Kyverno assistant as one OpenShell sandbox image: Hermes (pinned) plus the maintainer profile.
# Build from the repo root:  docker build -f container/Dockerfile -t kyverno-assistant:dev .
#
# Two stages. "build" installs and sets everything up, then removes what the finished image does not need
# (Python environments that Hermes' installer and plugin installs leave behind, and a download cache). "final"
# copies only the live files out of it, so those leftovers are in no layer of the image. Deleting files in a later
# layer would not shrink it, because the earlier layer would still hold them.

# Pinned by digest (the multi-architecture index), so a re-pushed tag cannot change what we build on.
# Dependabot proposes new digests; see docs/container.md, "Updating dependencies". Both stages use the same one.
FROM nvcr.io/nvidia/base/ubuntu:24.04@sha256:7e0f726d635d440d28a7f18e6498d3bdb76c7038bd4c90a090c21eb7c9f40686 AS build

# A failure anywhere in a piped command must fail the build (a failed download must not look like success).
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG HERMES_COMMIT=5307e93252ac655cd13fadbd561c0995142138c6

# libatomic1: the Node that Hermes installs needs it on arm64.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq less make python3 ripgrep unzip xz-utils libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Hermes keeps its install records and dependency venv under HERMES_HOME with absolute paths, so it
# runs from the directory it was installed into, owned by the sandbox user (uid 1000). The code in
# /opt/hermes stays root-owned and read-only.
ENV HOME=/opt/hermes-home
RUN mkdir -p "$HOME" \
    && curl -fsSL https://hermes-agent.nousresearch.com/install.sh \
       | bash -s -- --non-interactive --skip-browser \
           --dir /opt/hermes --hermes-home /opt/hermes-home/.hermes --commit "${HERMES_COMMIT}" \
    && chown -R 1000:1000 /opt/hermes-home \
    && chmod -R a+rX /opt/hermes \
    && ln -s /opt/hermes-home/.local/bin/hermes /usr/local/bin/hermes

# The profiles, baked in and read-only under /opt/kyverno. Both are installed into HERMES_HOME below, at build
# time; the safety hooks are run from here, where the agent cannot edit them.
COPY profiles/maintainer/SOUL.md profiles/maintainer/config.yaml profiles/maintainer/distribution.yaml profiles/maintainer/.env.example profiles/maintainer/.no-bundled-skills /opt/kyverno/profiles/maintainer/
COPY profiles/maintainer/hooks   /opt/kyverno/profiles/maintainer/hooks
COPY profiles/maintainer/plugins /opt/kyverno/profiles/maintainer/plugins
COPY profiles/maintainer/skills  /opt/kyverno/profiles/maintainer/skills
COPY profiles/maintainer/cron    /opt/kyverno/profiles/maintainer/cron
# The skill both profiles share for working on code (one source, installed with each profile).
COPY container/skills/workspace /opt/kyverno/profiles/maintainer/skills/workspace
COPY container/skills/workspace /opt/kyverno/profiles/contributor/skills/workspace
COPY profiles/contributor/SOUL.md profiles/contributor/config.yaml profiles/contributor/distribution.yaml profiles/contributor/.env.example profiles/contributor/.no-bundled-skills /opt/kyverno/profiles/contributor/
COPY profiles/contributor/hooks   /opt/kyverno/profiles/contributor/hooks
COPY profiles/contributor/plugins /opt/kyverno/profiles/contributor/plugins
COPY profiles/contributor/skills  /opt/kyverno/profiles/contributor/skills
COPY profiles/contributor/cron    /opt/kyverno/profiles/contributor/cron
COPY profiles/contributor/org     /opt/kyverno/profiles/contributor/org
COPY profiles/contributor/scripts /opt/kyverno/profiles/contributor/scripts
# The terminal runs locally inside the sandbox here, not over ssh into a second one.
COPY container/hooks/require-sandbox-terminal.sh /opt/kyverno/profiles/maintainer/hooks/require-sandbox-terminal.sh
COPY container/hooks/require-sandbox-terminal.sh /opt/kyverno/profiles/contributor/hooks/require-sandbox-terminal.sh
# The memory plugin's approval gate has a gap that the maintainer profile's hook closes; both profiles use that one hook.
COPY profiles/maintainer/hooks/block-mnemosyne-triples.sh /opt/kyverno/profiles/contributor/hooks/block-mnemosyne-triples.sh
RUN chmod 755 /opt/kyverno/profiles/*/hooks/* && chmod -R a+rX /opt/kyverno

# Install the profiles and the memory plugin (mnemosyne) now, while the network is open. The sandbox
# cannot reach PyPI, and at runtime Hermes would stop at a hidden "Prepare these dependencies? [y/N]"
# prompt. Hermes wants that answer on a terminal, so `script` supplies one. Runs as the sandbox user
# (uid 1000), so everything it writes into Hermes' home is already writable at runtime.
USER 1000:1000
RUN export HERMES_HOME=/opt/hermes-home/.hermes \
    && hermes profile install /opt/kyverno/profiles/maintainer --name kyverno -y \
    && printf 'y\n' | script -qec "hermes -p kyverno plugins install mnemosyne --enable" /dev/null \
    && PLUGINS="$(hermes -p kyverno plugins list 2>&1)" \
    && printf '%s\n' "$PLUGINS" | grep -q 'mnemosyne.*enabled' \
    && hermes profile install /opt/kyverno/profiles/contributor --name kyverno-contributor -y \
    && PLUGINS="$(hermes -p kyverno-contributor plugins list 2>&1)" \
    && printf '%s\n' "$PLUGINS" | grep -q 'mnemosyne.*enabled'
# The second profile does not run `plugins install mnemosyne`: that re-resolves Hermes' dependencies and builds a whole
# second Python environment (37 minutes and gigabytes). The plugin is already in the shared environment and the
# profile's config enables it; the verification at the end of the build proves its memory provider loads.

# Model-provider libraries. Hermes installs these on first use by re-resolving its whole dependency lock
# from PyPI and GitHub, which the sandbox cannot reach. OpenAI, NVIDIA, OpenRouter, Gemini and Copilot use
# the OpenAI library already in the image; Anthropic has its own, installed below next to the pin. It goes
# into the existing environment with uv: `hermes pm install --extra anthropic` builds a complete second copy of
# Hermes' Python environment, which added about 570 MB to the image.
#
# Then pin mnemosyne to the versions that work with this Hermes. mnemosyne-hermes 0.7.4 (3 Oct 2026) adds
# a per-home check that makes the memory provider fail to start ("loaded but no provider instance
# found"), and the plugin's own ranges (>=0.7.3, >=4.0.0b3) pick it up. The pin goes into whichever
# environment Hermes is actually running. Remove it when the Hermes pin moves to a version that works with
# newer mnemosyne.
#
# PyJWT is bumped for the same reason: Hermes pins 2.13.0, which has CVE-2026-102268 (authentication bypass via
# improper public key validation, fixed in 2.14.0). Hermes only verifies JWTs in dashboard sign-in providers this
# image does not use, so it was probably not reachable, but there is no reason to ship it. Remove the pin when the
# Hermes pin moves to a version that requires 2.14.0 or newer.
#
# Hermes also pins vulnerable certifi and Pillow releases, and fastembed currently caps Pillow below its fixed
# release. Install the fixed versions without dependency resolution so those constraints cannot restore them.
#
# The final lines fail the build if either library is not really usable: `anthropic` must import, and
# mnemosyne must come up as the memory provider (it fails silently otherwise).
RUN export HERMES_HOME=/opt/hermes-home/.hermes \
    && PY="$(ls -d /opt/hermes-home/.hermes/tools/python-*/bin/python3 | head -n 1)" \
    && UV="$(find /opt/hermes-home/.hermes/tools -name uv -type f -print -quit)" \
    && ACTIVE="$(HERMES_HOME=$HERMES_HOME/profiles/kyverno "$PY" -I -c 'import sys; sys.path.insert(0, "/opt/hermes"); import hermes_bootstrap, mnemosyne_hermes as m; print(m.__file__.split("/lib/python")[0])' | tail -n 1)" \
    && "$UV" pip install --python "$ACTIVE/bin/python" --prerelease=allow "mnemosyne-hermes==0.7.3" "mnemosyne-memory==4.0.0b3" "anthropic==0.87.0" "PyJWT[crypto]==2.14.0" \
    && "$UV" pip install --python "$ACTIVE/bin/python" --no-deps "certifi==2026.10.19" "Pillow==13.0.0" \
    && "$ACTIVE/bin/python" -I -c 'import certifi, PIL; from PIL import Image; image = Image.new("RGB", (2, 2)); assert image.resize((1, 1)).size == (1, 1); assert certifi.__version__ == "2026.10.19"; assert PIL.__version__ == "13.0.0"; print("certifi", certifi.__version__, "| Pillow", PIL.__version__, "| image resize passed")' \
    && HERMES_HOME=$HERMES_HOME/profiles/kyverno "$PY" -I -c '\
import sys; sys.path.insert(0, "/opt/hermes"); import hermes_bootstrap, anthropic; \
from plugins.memory import load_memory_provider; \
p = load_memory_provider("mnemosyne"); \
print("anthropic", anthropic.__version__, "| memory provider:", type(p).__name__ if p else None); \
sys.exit(0 if p else 1)'

# The memory plugin's embedding model (BAAI/bge-small-en-v1.5, the 64 MB quantized ONNX build). Mnemosyne's
# searches only ever read it from the profile's cache folder (local_files_only); the download happens in a
# separate first-start path that works on a laptop with internet. The sandbox cannot reach HuggingFace, so
# without this the first memory lookup stalls for a retry cycle and falls back to a weaker search.
# Download it now, into that same folder (the contributor profile reads the same files through a symlink).
RUN export HERMES_HOME=/opt/hermes-home/.hermes \
    && PY="$(ls -d /opt/hermes-home/.hermes/tools/python-*/bin/python3 | head -n 1)" \
    && "$PY" -I -c 'import sys; sys.path.insert(0, "/opt/hermes"); import hermes_bootstrap; from fastembed import TextEmbedding; v = list(TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir="/opt/hermes-home/.hermes/profiles/kyverno/cache/fastembed").embed(["warm up"]))[0]; print("embedding model ready, dimensions:", len(v)); sys.exit(0 if len(v) == 384 else 1)' \
    && mkdir -p /opt/hermes-home/.hermes/profiles/kyverno-contributor/cache \
    && ln -s /opt/hermes-home/.hermes/profiles/kyverno/cache/fastembed /opt/hermes-home/.hermes/profiles/kyverno-contributor/cache/fastembed

# The rest of the one-time profile setup (skills, hook paths, terminal and Slack settings): see the script.
COPY --chmod=755 container/setup-profile.sh /usr/local/bin/kyverno-setup-profile
RUN kyverno-setup-profile maintainer kyverno && kyverno-setup-profile contributor kyverno-contributor

# Keep certifi in pip's embedded copies and ensurepip's wheel free of the same vulnerable release.
RUN export HERMES_HOME=/opt/hermes-home/.hermes \
    && UV="$(find /opt/hermes-home/.hermes/tools -name uv -type f -print -quit)" \
    && PY="$(ls -d /opt/hermes-home/.hermes/tools/python-*/bin/python3 | head -n 1)" \
    && ACTIVE="$(HERMES_HOME=$HERMES_HOME/profiles/kyverno "$PY" -I -c 'import sys; sys.path.insert(0, "/opt/hermes"); import hermes_bootstrap, mnemosyne_hermes as m; print(m.__file__.split("/lib/python")[0])' | tail -n 1)" \
    && "$UV" pip install --python "$ACTIVE/bin/python" --upgrade "pip==26.2.2" \
    && for python in /opt/hermes-home/.hermes/tools/python-*/bin/python3 /opt/hermes-home/.local/share/uv/python/cpython-*/bin/python3; do \
         [ -x "$python" ] || continue; \
         "$UV" pip install --python "$python" --system --break-system-packages --upgrade "pip==26.2.2"; \
       done \
    && mkdir -p /tmp/pip-wheel \
    && "$ACTIVE/bin/python" -m pip download --no-deps --dest /tmp/pip-wheel "pip==26.2.2" \
    && for bundledir in /opt/hermes-home/.hermes/tools/python-*/lib/python*/ensurepip/_bundled /opt/hermes-home/.local/share/uv/python/cpython-*/lib/python*/ensurepip/_bundled; do \
         [ -d "$bundledir" ] || continue; \
         rm -f "$bundledir"/pip-*.whl; \
         cp /tmp/pip-wheel/pip-26.2.2-py3-none-any.whl "$bundledir/"; \
         sed -i 's/^_PIP_VERSION = ".*"/_PIP_VERSION = "26.2.2"/' "${bundledir%/_bundled}/__init__.py"; \
       done \
    && rm -rf /tmp/pip-wheel \
    && for python in "$ACTIVE/bin/python" /opt/hermes-home/.hermes/tools/python-*/bin/python3 /opt/hermes-home/.local/share/uv/python/cpython-*/bin/python3; do \
         [ -x "$python" ] || continue; \
         "$python" -I -c 'import pip, pip._vendor.certifi as c; assert pip.__version__ == "26.2.2" and c.__version__ == "2026.10.19"'; \
       done \
    && for ensurepip in /opt/hermes-home/.hermes/tools/python-*/lib/python*/ensurepip /opt/hermes-home/.local/share/uv/python/cpython-*/lib/python*/ensurepip; do \
         [ -d "$ensurepip" ] || continue; \
         grep -q '^_PIP_VERSION = "26.2.2"$' "$ensurepip/__init__.py"; \
         test -f "$ensurepip/_bundled/pip-26.2.2-py3-none-any.whl"; \
       done

# Keep only the Python environment Hermes is running. The installer and each plugin install leave the one they
# replaced behind (about 0.9 GB here), and `hermes pm gc` treats them as still referenced, so they are removed
# by hand. They are only history for an in-place repair or rollback, which needs the network; the sandbox has none,
# and an upgrade is a new image. The package download cache is only used while installing. If anything depended on
# what is removed, the proofs in the final stage fail the build.
RUN export HERMES_HOME=/opt/hermes-home/.hermes \
    && PY="$(ls -d /opt/hermes-home/.hermes/tools/python-*/bin/python3 | head -n 1)" \
    && ACTIVE_ID="$(HERMES_HOME=$HERMES_HOME/profiles/kyverno "$PY" -I -c 'import sys; sys.path.insert(0, "/opt/hermes"); import hermes_bootstrap, mnemosyne_hermes as m; print(m.__file__.split("/venv/")[0].split("/")[-1])' | tail -n 1)" \
    && test -n "$ACTIVE_ID" \
    && for d in /opt/hermes-home/.hermes/installs/*/environments/*; do \
         if [ "$(basename "$d")" != "$ACTIVE_ID" ]; then echo "removing superseded environment $(basename "$d")"; rm -rf "$d"; fi; \
       done \
    && rm -rf /opt/hermes-home/.hermes/cache/uv


# ---------------------------------------------------------------------------------------------------------
FROM nvcr.io/nvidia/base/ubuntu:24.04@sha256:7e0f726d635d440d28a7f18e6498d3bdb76c7038bd4c90a090c21eb7c9f40686 AS final

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The tools the agent's own terminal uses, plus libatomic1 for the Node that Hermes ships.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq less make python3 ripgrep unzip xz-utils libatomic1 \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/opt/hermes-home
# Hermes' code and the profile stay root-owned; Hermes' home belongs to the sandbox user (uid 1000).
COPY --from=build /opt/hermes /opt/hermes
COPY --from=build /opt/kyverno /opt/kyverno
# Only for the build-time check below: the proxy's tool allowlists live in these provider profiles, outside the sandbox.
COPY --chown=1000:1000 container/providers/repo-github.yaml container/providers/repo-github-contributor.yaml /tmp/providers/
COPY --from=build --chown=1000:1000 /opt/hermes-home /opt/hermes-home
RUN ln -s /opt/hermes-home/.local/bin/hermes /usr/local/bin/hermes
COPY --chmod=755 container/kyverno-start container/kyverno-main container/kyverno-env container/dashboard-relay /usr/local/bin/
# Clone a repository and install the toolchains it declares (Go, Node, the Kyverno CLI), for building and testing in the sandbox.
COPY --chmod=755 container/kyverno-workspace /usr/local/bin/kyverno-workspace
COPY --chmod=755 container/toolchains /usr/local/lib/kyverno/toolchains

# The image's final user must stay uid 1000: OpenShell refuses an image whose default user is root.
USER 1000:1000

# Proof that the finished image works, run on the copied files with NO network (see container/verify-image.sh).
COPY --chmod=755 container/verify-image.sh /usr/local/bin/kyverno-verify-image
RUN --network=none kyverno-verify-image
