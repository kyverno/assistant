#!/bin/bash
# pre_tool_call hook. Hermes pipes the event as JSON on stdin. config.yaml's
# matcher already narrows which invocations happen, but this script also
# checks tool_name itself — defense-in-depth, not double work, so a matcher
# gap doesn't turn into "block everything" or "block nothing." Uses python3
# (guaranteed by Hermes' own installer) instead of jq (not guaranteed).
set -euo pipefail

python3 -c '
import json, re, sys

payload = json.load(sys.stdin)
tool = payload.get("tool_name") or ""

if re.search(r"merge|delete_repo|force_push", tool):
    print(json.dumps({
        "action": "block",
        "message": f"{tool} is not permitted for kyverno-assistant — see SOUL.md",
    }))
else:
    print("{}")
'
