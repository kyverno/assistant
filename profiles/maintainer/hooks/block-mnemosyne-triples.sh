#!/bin/bash
# pre_tool_call hook. Backstop for a real gap found live: memory.write_approval
# correctly stages mnemosyne_remember (a real pending file appears on disk) but
# mnemosyne_triple_add commits straight to the database, bypassing the gate
# entirely (verified: no pending file, the row lands immediately in the real
# triples table). Since the plugin's own approval gate can't be trusted
# uniformly across its tools, this hook enforces the one thing that actually
# matters here at the code level: only the predicates docs/v2-plan.md's Phase 8
# design actually uses are allowed through. config.yaml's matcher already
# narrows which invocations reach this script — defense-in-depth, not double
# work, so a matcher gap doesn't turn into "block everything" or "block
# nothing." Uses python3 (guaranteed by Hermes' own installer) instead of jq.
set -euo pipefail

python3 -c '
import json, sys

payload = json.load(sys.stdin)
tool = payload.get("tool_name") or ""
tool_input = payload.get("tool_input") or {}

if tool == "mnemosyne_triple_add":
    allowed = {
        "caused_e2e_failure",
        "e2e_failure_opened",
        "e2e_failure_closed",
        "excludes",
        "prioritizes",
    }
    predicate = tool_input.get("predicate") or ""
    if predicate not in allowed:
        print(json.dumps({
            "action": "block",
            "message": (
                f"mnemosyne_triple_add predicate {predicate!r} is not in the "
                f"allowed set {sorted(allowed)} — see docs/v2-plan.md Phase 8. "
                "write_approval does not gate this tool, so this list is the "
                "real control; extend it deliberately, not by editing this "
                "message away."
            ),
        }))
        sys.exit(0)

print("{}")
'
