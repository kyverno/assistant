#!/bin/bash
# Build-time proof that the finished image works, run on the copied files with NO network (the Dockerfile runs it
# with --network=none). If the cleanup removed something that was needed, or something needs the internet, the build
# fails and the output says what. Both profiles are checked: the safety hooks cannot be written by the agent, the
# memory provider comes up, the proxy's GitHub tool allowlist equals the one in the profile's config.yaml, and a
# full memory cycle passes (import a memory, search it, which reads the embedding model locally, and consolidate).
# The warm-up data is removed at the end, so every user starts with an empty memory.
set -euo pipefail

export HERMES_HOME=/opt/hermes-home/.hermes
PY="$(compgen -G "$HERMES_HOME/tools/python-*/bin/python3" | head -n 1)"
PROFILES="maintainer:kyverno:repo-github contributor:kyverno-contributor:repo-github-contributor"
HOOK_EVENT='{"tool_name":"merge_pull_request","tool_input":{}}'

V="$(hermes --version)"
echo "$V"
case "$V" in *unknown*) echo "Hermes cannot tell its own version" >&2; exit 1 ;; esac
test ! -w /opt/hermes

for entry in $PROFILES; do
  IFS=: read -r kind name provider <<<"$entry"
  echo "== $kind ($name)"
  test ! -w "/opt/kyverno/profiles/$kind/hooks"
  for f in /opt/kyverno/profiles/"$kind"/hooks/*; do test ! -w "$f"; done

  PD="$HERMES_HOME/profiles/$name"
  HERMES_HOME=$PD "$PY" -I -c '
import sys; sys.path.insert(0, "/opt/hermes")
import hermes_bootstrap, anthropic, certifi, PIL, pip
import pip._vendor.certifi as pip_certifi
from PIL import Image
assert certifi.__version__ == "2026.10.19" and pip_certifi.__version__ == "2026.10.19"
assert PIL.__version__ == "13.0.0" and pip.__version__ == "26.2.2"
assert Image.new("RGB", (2, 2)).resize((1, 1)).size == (1, 1)
from plugins.memory import load_memory_provider
p = load_memory_provider("mnemosyne")
print("anthropic", anthropic.__version__, "| certifi", certifi.__version__, "| Pillow", PIL.__version__, "| pip", pip.__version__, "| memory provider:", type(p).__name__ if p else None)
sys.exit(0 if p else 1)'

  HERMES_HOME=$PD "$PY" -I -c '
import re, sys
cfg = open(sys.argv[1] + "/config.yaml").read()
want = re.search(r"X-MCP-Tools: \"([^\"]+)\"", cfg).group(1).split(",")
prof = open("/tmp/providers/" + sys.argv[2] + ".yaml").read()
have = [t.strip() for t in re.search(r"any: \[(.*?)\]", prof, re.S).group(1).split(",") if t.strip()]
print("proxy tool allowlist matches config.yaml:", have == want, "(%d tools)" % len(want))
sys.exit(0 if have == want else 1)' "$PD" "$provider"

  printf '%s' '{"mnemosyne_export": {"version": "1.3"}, "working_memory": [{"id": "warmup:1", "content": "Kyverno warm-up memory: the review queue is ordered by dependency tier.", "source": "warmup", "timestamp": "2026-01-01T00:00:00", "session_id": "warmup", "importance": 0.9, "scope": "global", "pinned": 1}]}' > /tmp/warmup.json
  hermes -p "$name" mnemosyne import -i /tmp/warmup.json --force
  OUT="$(hermes -p "$name" mnemosyne inspect 'dependency tier review queue' 2>&1)"
  echo "$OUT"
  if echo "$OUT" | grep -q 'ERROR'; then echo "memory check reported an error" >&2; exit 1; fi
  echo "$OUT" | grep -q 'warm-up memory'
  hermes -p "$name" mnemosyne sleep
  rm -rf /tmp/warmup.json "$HERMES_HOME/profiles/$name/mnemosyne/data"
done

echo "== contributor: guard hook and organisation file"
# The hook must start under Hermes' interpreter and refuse a merge without touching the network.
OUT="$(echo "$HOOK_EVENT" | "$PY" -I /opt/kyverno/profiles/contributor/hooks/contributor-guard.py)"
echo "$OUT"
echo "$OUT" | grep -q '"action": "block"'
HERMES_HOME=$HERMES_HOME/profiles/kyverno-contributor "$PY" -I - <<'PYEOF'
import importlib.util, sys
sys.path.insert(0, "/opt/hermes"); import hermes_bootstrap
plugin = "/opt/hermes-home/.hermes/profiles/kyverno-contributor/plugins/contributor"
spec = importlib.util.spec_from_file_location("contributor", plugin + "/__init__.py", submodule_search_locations=[plugin])
module = importlib.util.module_from_spec(spec); sys.modules["contributor"] = module; spec.loader.exec_module(module)
from contributor import rules, tools
org = rules.load_org()
print("organisation file loads:", org["name"], "|", len(tools.TOOLS), "tools")
PYEOF

echo "== workspace: script, toolchains and the shared skill"
test -x /usr/local/bin/kyverno-workspace
for f in /usr/local/lib/kyverno/toolchains/*.sh /usr/local/bin/kyverno-workspace; do bash -n "$f"; done
test -f "$HERMES_HOME/profiles/kyverno/skills/workspace/SKILL.md"
test -f "$HERMES_HOME/profiles/kyverno-contributor/skills/workspace/SKILL.md"
# The push guard refuses a force push and a push to main, and lets a normal push to the fork through.
for kind in maintainer contributor; do
  GUARD=/opt/kyverno/profiles/$kind/hooks/git-push-guard.py
  echo '{"tool_name":"terminal","tool_input":{"command":"git push --force fork x"}}' | python3 -I "$GUARD" | grep -q '"action": "block"'
  echo '{"tool_name":"terminal","tool_input":{"command":"git push fork main"}}' | python3 -I "$GUARD" | grep -q '"action": "block"'
  echo '{"tool_name":"terminal","tool_input":{"command":"git push origin x"}}' | python3 -I "$GUARD" | grep -q '"action": "block"'
  test "$(echo '{"tool_name":"terminal","tool_input":{"command":"git push -u fork fix/x"}}' | python3 -I "$GUARD")" = '{}'
done
# The tools it downloads are fetched at run time (the build has no network); everything it needs to do that is here.
for tool in curl jq tar xz sha256sum git make; do command -v "$tool" >/dev/null; done

rm -rf /tmp/providers
