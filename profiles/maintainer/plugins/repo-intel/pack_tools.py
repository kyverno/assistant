"""Tool schema + handler for `repo_pack`: what the organisation pack says, so skills carry method and the
pack carries the organisation's own rules and facts."""

from __future__ import annotations

import json
from typing import Any, Dict

from . import pack

REPO_PACK_SCHEMA = {
    "name": "repo_pack",
    "description": (
        "The organisation pack for this repository: its branch gates, file classes (generated, API surface, "
        "critical, test-only), generated-file inputs, dependency bots, milestone format and urgency labels, "
        "readiness labels, review bots, docs globs, and its own prose facts (facts). Call it once per session "
        "before using any of those; never assume conventions from another repository. `pack: none` means the "
        "repository has no pack: say so once, then work from the live repository only (labels, CODEOWNERS, "
        "docs) and say which signals are therefore missing (no file classes, no gate)."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def handle_repo_pack(args: Dict[str, Any], **_kw) -> str:
    try:
        rules = pack.load()
    except pack.PackError as exc:
        return json.dumps({"success": False, "error": f"organisation pack: {exc}"})
    return json.dumps({"success": True, **rules.summary(), "facts": rules.facts})
