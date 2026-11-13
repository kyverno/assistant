"""Tool schema + handler for `update_dashboard`. Thin: validate via store, write, return JSON.
Skills hand it already-synthesized items, not raw fetch output."""

from __future__ import annotations

import json
from typing import Any, Dict

from . import store

UPDATE_DASHBOARD_SCHEMA = {
    "name": "update_dashboard",
    "description": (
        "Write what you just presented to the maintainer onto their dashboard tab, so it is "
        "there without scrolling the session. Call it after presenting a queue, brief, or "
        "triage, and after every confirmed GitHub action. Re-sending an item or action "
        "updates it in place.\n"
        "Sections (data shape for each):\n"
        "- items: {items:[{key:'pr:123'|'issue:45'|'discussion:6', title, url, author, "
        "status: open|merged|closed|draft, "
        "state:{ci,merge,size,milestone,labels:[..],stale}, verdict:{action: "
        "close|rescue|decide|review|defer|approve|triage|done, reason}, summary, "
        "needs_you (one line), draft (comment text awaiting confirm), threads:[{text,url}], "
        "related:[{key, rel, state: open|merged|closed|draft, title?, url?}]}]} — rel is this "
        "item's relation to the other: closes|closed-by|supersedes|superseded-by|depends-on|"
        "blocks|competes|duplicates|discussed-in (e.g. issue:15350 lists pr:17344 as "
        "closed-by; the reverse edge is derived, send one side only). Send only fields you "
        "know; omitted fields keep their old value.\n"
        "- views: {name, title, description, groups:[{id,label,hint}], entries:[{key, group, "
        "tier, reason}]} — an ordered list of item keys (e.g. name 'queue', 'stale-1.19.2', "
        "'triage'); replaces the view of that name. Send the items first.\n"
        "- actions: {actions:[{key, action: approved|requested-changes|commented|nudged|"
        "closed|labeled|deferred|..., summary, link}]} — confirmed actions only.\n"
        "- session: {focus, milestone, milestone_due, gate, last_session, counts:{..}, "
        "note} — header fields, merged."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section": {"type": "string", "enum": list(store.SECTIONS)},
            "data": {"type": "object", "description": "Shape depends on section; see tool description."},
        },
        "required": ["section", "data"],
        "additionalProperties": False,
    },
}


def handle_update_dashboard(args: Dict[str, Any], **_kw) -> str:
    try:
        result = store.update(str(args.get("section") or ""), args.get("data"))
    except (ValueError, TypeError, AttributeError) as exc:
        return json.dumps({"success": False, "error": str(exc)})
    except OSError as exc:
        return json.dumps({"success": False, "error": f"could not write dashboard state: {exc}"})
    return json.dumps({"success": True, **result})
