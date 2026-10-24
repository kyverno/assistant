#!/bin/bash
# pre_tool_call hook (container variant). Terminal and file tools may only run inside an OpenShell
# sandbox: the supervisor creates /run/openshell-supervisor-ca there, and the sandbox user cannot
# create it (the filesystem policy keeps /run read-only). Anywhere else the call is blocked, so a
# copy of this profile run on a laptop can never get a terminal on the host.
#
# One opt-in exception: container/kyverno --no-openshell --allow-terminal runs the image in plain Docker and sets
# KYVERNO_RUNTIME=docker and KYVERNO_ALLOW_TERMINAL=1 on the container. Those come from `docker run`, so the agent
# cannot change them. Without both, the tools stay blocked.
set -euo pipefail
cat > /dev/null
if [ -d /run/openshell-supervisor-ca ] \
   || { [ "${KYVERNO_RUNTIME:-}" = docker ] && [ "${KYVERNO_ALLOW_TERMINAL:-}" = 1 ]; }; then
  echo '{}'
else
  echo '{"action":"block","message":"terminal and file tools are only allowed inside the OpenShell sandbox (or with container/kyverno --no-openshell --allow-terminal)"}'
fi
