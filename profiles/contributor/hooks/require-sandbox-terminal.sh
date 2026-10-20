#!/bin/bash
# pre_tool_call hook. Terminal commands may only run when the profile's terminal
# backend is ssh into the OpenShell sandbox; anything else would run on the
# maintainer's own machine. Checked on every call, so a changed config can't
# silently move the terminal back to the host.
set -euo pipefail

python3 -c '
import json, os, sys

payload = json.load(sys.stdin)
cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[1]))), "config.yaml")
try:
    import yaml
    terminal = (yaml.safe_load(open(cfg_path)) or {}).get("terminal") or {}
except Exception:
    terminal = {}

host = str(terminal.get("ssh_host") or "")
if terminal.get("backend") == "ssh" and host.startswith("openshell-"):
    print("{}")
else:
    print(json.dumps({
        "action": "block",
        "message": "terminal is only allowed inside the OpenShell sandbox — run scripts/openshell.sh status",
    }))
' "$0"
