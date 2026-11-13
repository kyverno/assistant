#!/bin/bash
# Checks on a built image, beyond the ones the Dockerfile already runs (container/verify-image.sh): what the image does as a
# user meets it. Usage: tests/image/run.sh IMAGE   (the image must be present locally)
set -uo pipefail

IMAGE="${1:?usage: tests/image/run.sh IMAGE}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES="$HERE/../fixtures"
FAILED=0
ok()  { printf '  ok    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; FAILED=1; }
check() {
  local what="$1" out; shift
  if out="$("$@" 2>&1)"; then ok "$what"; else bad "$what"; printf '%s\n' "$out" | tail -n 8 | sed 's/^/          /'; fi
}

# shellcheck disable=SC2119,SC2120  # the arguments are docker run options, and none are needed most of the time
# Run a shell snippet in the image as the sandbox user, with Hermes' Python as $PY and the profile folders set up.
inimage() {
  docker run --rm -i --entrypoint bash -e HERMES_HOME=/opt/hermes-home/.hermes -v "$FIXTURES":/fixtures:ro "$@" "$IMAGE" -s
}
# shellcheck disable=SC2016  # the snippet is expanded inside the image, not here
PRELUDE='PY="$(compgen -G "$HERMES_HOME/tools/python-*/bin/python3" | head -n 1)"; MPLUG=$HERMES_HOME/profiles/maintainer/plugins; HOOKS=/opt/kyverno/profiles'

echo "== launcher, as the installer extracts it"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
docker run --rm "$IMAGE" tar -C /opt/kyverno -c launcher | tar -x -C "$TMP"
check "the launcher is called assistant" test -x "$TMP/launcher/assistant"
check "the Docker launcher is called assistant-docker" test -x "$TMP/launcher/assistant-docker"
check "no launcher is left under the old name" bash -c "! test -e '$TMP/launcher/kyverno' && ! test -e '$TMP/launcher/kyverno-docker'"
check "the launcher prints its help" bash -c "'$TMP/launcher/assistant' --help | grep -q -- '--profile'"
check "the provider profiles are there" bash -c "test -f '$TMP/launcher/providers/repo-github.yaml' && grep -q 'id: assistant-repo-github' '$TMP/launcher/providers/repo-github.yaml'"
check "no provider id keeps the old prefix" bash -c "! grep -rq 'id: kyverno-' '$TMP/launcher/providers'"
check "the image has no kyverno-workspace alias" docker run --rm --entrypoint sh "$IMAGE" -c '! test -e /usr/local/bin/kyverno-workspace && test -x /usr/local/bin/assistant-workspace'

echo "== both profiles are installed, with the plugins they need"
for profile in maintainer contributor; do
  check "$profile profile exists" inimage <<<"$PRELUDE; test -f \$HERMES_HOME/profiles/$profile/config.yaml"
done
check "maintainer has repo-intel and the dashboard" inimage <<<"$PRELUDE; test -f \$MPLUG/repo-intel/plugin.yaml && test -f \$MPLUG/maintainer-dashboard/plugin.yaml"
check "contributor has its plugin" inimage <<<"$PRELUDE; test -f \$HERMES_HOME/profiles/contributor/plugins/contributor/plugin.yaml"

echo "== the organisation pack"
check "repo_pack reads the pack from the image" inimage <<<"$PRELUDE
HERMES_HOME=\$HERMES_HOME/profiles/maintainer \$PY -I - <<'PYEOF'
import importlib.util, json, sys
sys.path.insert(0, '/opt/hermes'); import hermes_bootstrap
d = '/opt/hermes-home/.hermes/profiles/maintainer/plugins/repo-intel'
spec = importlib.util.spec_from_file_location('repo_intel', d + '/__init__.py', submodule_search_locations=[d])
m = importlib.util.module_from_spec(spec); sys.modules['repo_intel'] = m; spec.loader.exec_module(m)
from repo_intel import pack_tools
out = json.loads(pack_tools.handle_repo_pack({}))
assert out['success'] and out['branch_gates'], out
assert out['pack'] == '/opt/kyverno/profiles/maintainer/org/org.yaml', out['pack']
PYEOF"
check "another organisation's pack works from a file, and sequences PRs" inimage <<<"$PRELUDE
HERMES_HOME=\$HERMES_HOME/profiles/maintainer \$PY -I - <<'PYEOF'
import importlib.util, sys
sys.path.insert(0, '/opt/hermes'); import hermes_bootstrap
d = '/opt/hermes-home/.hermes/profiles/maintainer/plugins/repo-intel'
spec = importlib.util.spec_from_file_location('repo_intel', d + '/__init__.py', submodule_search_locations=[d])
m = importlib.util.module_from_spec(spec); sys.modules['repo_intel'] = m; spec.loader.exec_module(m)
from repo_intel import pack, sequencer
rules = pack.load('/fixtures/other-org.yaml')
assert rules.gate.name == 'release-gate' and rules.classify('core/x.go') == 'ADMISSION_CRITICAL'
r = sequencer.sequence([{'number': 1, 'changed_files': ['gen/out.go']}, {'number': 2, 'changed_files': ['schema/a.proto']}], 'main', rules=rules)
assert [p['pr'] for t in r['tiers'] for p in t['prs']] == [2, 1], r
PYEOF"
check "no pack means empty rules, not an error" inimage <<<"$PRELUDE
HERMES_HOME=\$HERMES_HOME/profiles/maintainer \$PY -I - <<'PYEOF'
import importlib.util, sys
sys.path.insert(0, '/opt/hermes'); import hermes_bootstrap
d = '/opt/hermes-home/.hermes/profiles/maintainer/plugins/repo-intel'
spec = importlib.util.spec_from_file_location('repo_intel', d + '/__init__.py', submodule_search_locations=[d])
m = importlib.util.module_from_spec(spec); sys.modules['repo_intel'] = m; spec.loader.exec_module(m)
from repo_intel import pack
r = pack.load('/nonexistent/org.yaml')
assert r.gates == () and r.classify('anything.go') == 'STANDARD'
PYEOF"
check "the pack in the image cannot be changed by the agent" docker run --rm --entrypoint sh "$IMAGE" -c '! test -w /opt/kyverno/profiles/maintainer/org/org.yaml'

echo "== the safety hooks, inside the image"
# hook_out KIND RUNNER SCRIPT EVENT: what the hook answers for that event ($PY -I runs a Python hook, bash a shell one).
hook_out() { inimage <<<"$PRELUDE; echo '$4' | HERMES_HOME=\$HERMES_HOME/profiles/$1 $2 /opt/kyverno/profiles/$1/hooks/$3"; }
blocks() { grep -q '"action": *"block"' <<<"$(hook_out "$@")"; }
allows() { [ "$(hook_out "$@")" = '{}' ]; }
# shellcheck disable=SC2016
PYRUN='$PY -I'    # expanded inside the image
MERGE='{"tool_name":"merge_pull_request","tool_input":{}}'
triple() { echo '{"tool_name":"mnemosyne_triple_add","tool_input":{"predicate":"'"$1"'"}}'; }
check "a merge is blocked (maintainer)" blocks maintainer bash block-dangerous-tools.sh "$MERGE"
check "a merge is blocked (contributor)" blocks contributor "$PYRUN" contributor-guard.py "$MERGE"
check "the gate_* memory triples are allowed" allows maintainer bash block-mnemosyne-triples.sh "$(triple caused_gate_failure)"
check "other memory triples are blocked" blocks maintainer bash block-mnemosyne-triples.sh "$(triple likes)"
check "the terminal is blocked without the sandbox" blocks maintainer bash require-sandbox-terminal.sh '{"tool_name":"terminal","tool_input":{}}'
check "a force push is blocked" blocks maintainer "$PYRUN" git-push-guard.py '{"tool_name":"terminal","tool_input":{"command":"git push --force fork x"}}'
check "a push to the fork is allowed" allows maintainer "$PYRUN" git-push-guard.py '{"tool_name":"terminal","tool_input":{"command":"git push -u fork fix/x"}}'

echo "== state survives a new container"
VOL="assistant-check-$$"
docker volume create "$VOL" >/dev/null
# A new volume belongs to root; the sandbox user (1000) has to be able to write to it.
docker run --rm --user root --entrypoint chown -v "$VOL":/state "$IMAGE" 1000:1000 /state
check "dashboard state is written" inimage -v "$VOL":/state <<<"$PRELUDE
HERMES_HOME=/state \$PY -I - <<'PYEOF'
import importlib.util, sys
sys.path.insert(0, '/opt/hermes'); import hermes_bootstrap
d = '/opt/hermes-home/.hermes/profiles/maintainer/plugins/maintainer-dashboard'
spec = importlib.util.spec_from_file_location('md', d + '/__init__.py', submodule_search_locations=[d])
m = importlib.util.module_from_spec(spec); sys.modules['md'] = m; spec.loader.exec_module(m)
from md import store
store.update('items', {'items': [{'key': 'pr:1', 'kind': 'pr', 'number': 1, 'title': 'x', 'verdict': 'review'}]})
PYEOF"
check "and read by the next container" inimage -v "$VOL":/state <<<"$PRELUDE
test -f /state/maintainer-dashboard/state.json && grep -q 'pr:1' /state/maintainer-dashboard/state.json"
docker volume rm "$VOL" >/dev/null 2>&1

echo
if [ "$FAILED" = 0 ]; then echo "all image checks passed"; else echo "some image checks FAILED"; exit 1; fi
