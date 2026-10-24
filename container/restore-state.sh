#!/bin/bash
# Runs inside the NEW sandbox during `container/kyverno --upgrade`: puts back what the old one saved.
# The launcher uploads this script and /sandbox/kyverno-state.tar, then runs it. Only your state comes back:
# memory, chat history, your repo and username, the Copilot sign-in, and the model you chose. The profile
# itself (skills, hooks, plugins, settings) stays exactly as the new image ships it.
set -uo pipefail

export HERMES_HOME=/opt/hermes-home/.hermes
P="$HERMES_HOME/profiles/kyverno"
TMP=/tmp/kyverno-restore
# Where the launcher put the bundle: /sandbox under OpenShell; the Docker launcher (--no-openshell) sets this.
STATE_TAR="${KYVERNO_STATE_TAR:-/sandbox/kyverno-state.tar}"

rm -rf "$TMP" && mkdir -p "$TMP"
tar -xf "$STATE_TAR" -C "$TMP" || { echo "could not read the saved state" >&2; exit 1; }

# 1. data directories and files, replaced wholesale (the new image starts with empty ones)
for d in mnemosyne/data memories sessions kyverno-dashboard; do
  if [ -e "$TMP/$d" ]; then
    mkdir -p "$P/$(dirname "$d")"
    rm -rf "${P:?}/$d"
    cp -a "$TMP/$d" "$P/$d"
    echo "  restored $d"
  fi
done
if [ -e "$TMP/state.db" ]; then
  rm -f "$P"/state.db "$P"/state.db-wal "$P"/state.db-shm
  for f in state.db state.db-wal state.db-shm; do [ -e "$TMP/$f" ] && cp -a "$TMP/$f" "$P/$f"; done
  echo "  restored chat history"
fi
[ -e "$TMP/.kyverno-model-chosen" ] && cp -a "$TMP/.kyverno-model-chosen" "$P/.kyverno-model-chosen"

# 2. only the user's own settings from the old .env; the new image's own lines are kept
if [ -f "$TMP/.env" ]; then
  OLD_ENV="$TMP/.env" NEW_ENV="$P/.env" python3 - <<'PY'
import os
KEYS = ("COPILOT_GITHUB_TOKEN", "KYVERNO_REPO", "MAINTAINER_GITHUB_LOGIN")
old = {}
for line in open(os.environ["OLD_ENV"]):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        old[k.strip()] = v.strip()
path = os.environ["NEW_ENV"]
lines = open(path).read().splitlines() if os.path.exists(path) else []
done = []
for key in KEYS:
    if not old.get(key):
        continue
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={old[key]}"
            break
    else:
        lines.append(f"{key}={old[key]}")
    done.append(key)
open(path, "w").write("\n".join(lines) + "\n")
print("  restored settings:", ", ".join(done) or "none")
PY
fi

# 3. the model choice from the old config.yaml (only these four keys), applied through Hermes itself
if [ -f "$TMP/config.yaml" ]; then
  python3 - "$TMP/config.yaml" > "$TMP/model.txt" <<'PY'
import re, sys
s = open(sys.argv[1]).read()
m = re.search(r'(?m)^model:\n((?:[ \t]+.*\n|\n)+)', s)
if m:
    for line in m.group(1).splitlines():
        mm = re.match(r'\s+(default|provider|base_url|api_mode):\s*"?([^"#\n]+?)"?\s*(#.*)?$', line)
        if mm:
            print(mm.group(1), mm.group(2))
PY
  while read -r key value; do
    [ -n "$key" ] && timeout 120 hermes -p kyverno config set "model.$key" "$value" </dev/null >/dev/null 2>&1
  done < "$TMP/model.txt"
  echo "  restored model: $(tr '\n' ' ' < "$TMP/model.txt")"
fi

rm -rf "$TMP" "$STATE_TAR" /sandbox/restore-state.sh "$0"
