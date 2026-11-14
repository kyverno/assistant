"""Tool schema + handler for `repo_pack`: what the organisation pack says, so skills carry method and the
pack carries the organisation's own rules and facts."""

from __future__ import annotations

import json
from typing import Any, Dict

from . import pack
from .fetch_tools import _repo

REPO_PACK_SCHEMA = {
    "name": "repo_pack",
    "description": (
        "The organisation pack for this repository: its branch gates, file classes (generated, API surface, "
        "critical, test-only), generated-file inputs, dependency bots, milestone format and urgency labels, "
        "readiness labels, review bots, docs globs, and its own prose facts (facts). Call it once per session "
        "before using any of those; never assume conventions from another repository. The pack is the "
        "organisation's own, published as assistant-org.yaml in its .github repository. `pack: none` means the "
        "organisation has published none: say so once, and that it can add one (docs/organisation-pack.md), then work from the live repository only (labels, CODEOWNERS, "
        "docs) and say which signals are therefore missing (no file classes, no gate)."
    ),
    "parameters": {"type": "object", "properties": {
        "repo": {"type": "string", "description": "'owner/repo' to read the pack for. Omit for the profile's repository."}},
        "additionalProperties": False},
}


def handle_repo_pack(args: Dict[str, Any], **_kw) -> str:
    try:
        rules = pack.load(repo=_repo(args))
    except pack.PackError as exc:
        return json.dumps({"success": False, "error": f"organisation pack: {exc}"})
    return json.dumps({"success": True, **rules.summary(), "facts": rules.facts})
