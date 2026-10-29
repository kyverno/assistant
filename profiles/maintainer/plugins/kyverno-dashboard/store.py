"""Dashboard state: one JSON file under the profile's Hermes home, shared by the agent-side tool
(tools.py) and the dashboard API (dashboard/plugin_api.py). Stdlib only."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List

SECTIONS = ("items", "views", "actions", "session")
KINDS = ("pr", "issue", "discussion")
MAX_ACTIONS = 500


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def state_path() -> Path:
    return _home() / "kyverno-dashboard" / "state.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_state() -> Dict[str, Any]:
    return {"items": {}, "views": {}, "actions": [], "session": {}, "updated_at": None}


def load() -> Dict[str, Any]:
    try:
        with open(state_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_state()
    return {**empty_state(), **data}


@contextmanager
def _locked() -> Iterator[None]:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _save(state: Dict[str, Any]) -> None:
    state["updated_at"] = now()
    path = state_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def _check_key(key: Any) -> str:
    kind, _, num = str(key).partition(":")
    kind, num = kind.strip().lower(), num.strip().lstrip("#").strip()
    if kind not in KINDS or not num:
        raise ValueError(f"item key must look like 'pr:123' / 'issue:45' / 'discussion:6', got {key!r}")
    # 'pr:#07' and 'pr:7' are the same item: never let spelling create a second card
    return f"{kind}:{num.lstrip('0') or '0'}" if num.isdigit() else f"{kind}:{num}"


def _merge_related(old: List[Any], new: List[Any]) -> List[Dict[str, Any]]:
    """Edges are identified by (key, rel); a re-send updates them and keeps the ones not re-sent."""
    out: Dict[Any, Dict[str, Any]] = {}
    for r in list(old or []) + list(new or []):
        if isinstance(r, dict) and r.get("rel"):
            k = _check_key(r.get("key"))
            out[(k, r["rel"])] = {**out.get((k, r["rel"]), {}), **r, "key": k}
    return list(out.values())


def _upsert_items(state: Dict[str, Any], data: Dict[str, Any]) -> int:
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("data.items must be a non-empty array")
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("each item must be an object")
        key = _check_key(raw.get("key"))
        cur = state["items"].get(key, {})
        # fields sent replace the old value, fields omitted keep it; nested state/related merge
        merged = {**cur, **raw, "key": key, "updated_at": now()}
        if isinstance(raw.get("state"), dict):
            merged["state"] = {**(cur.get("state") or {}), **raw["state"]}
        if "related" in raw:
            merged["related"] = _merge_related(cur.get("related"), raw.get("related"))
        if "acted" in cur:
            merged["acted"] = cur["acted"]  # only update_dashboard(actions) may set this
        state["items"][key] = merged
    return len(items)


def _set_view(state: Dict[str, Any], data: Dict[str, Any]) -> int:
    name = str(data.get("name") or "").strip()
    entries = data.get("entries")
    if not name or not isinstance(entries, list):
        raise ValueError("data needs a 'name' and an 'entries' array")
    clean: List[Dict[str, Any]] = []
    for e in entries:
        e = dict(e) if isinstance(e, dict) else {"key": e}
        e["key"] = _check_key(e.get("key"))
        clean.append(e)
    state["views"][name] = {
        "name": name,
        "title": data.get("title") or name,
        "description": data.get("description") or "",
        "groups": data.get("groups") or [],
        "entries": clean,
        "updated_at": now(),
    }
    return len(clean)


def _log_actions(state: Dict[str, Any], data: Dict[str, Any]) -> int:
    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("data.actions must be a non-empty array")
    for raw in actions:
        key = _check_key(raw.get("key"))
        entry = {
            "ts": now(),
            "key": key,
            "action": str(raw.get("action") or "").strip(),
            "summary": raw.get("summary") or "",
            "link": raw.get("link") or "",
        }
        if not entry["action"]:
            raise ValueError("each action needs an 'action' (e.g. approved, nudged, closed)")
        # the same write re-sent is an update in place (original timestamp kept), not a second
        # entry. Identity is the link; with no link, the summary tells two real nudges apart.
        def same(a: Dict[str, Any]) -> bool:
            if a["key"] != key or a["action"] != entry["action"]:
                return False
            return a["link"] == entry["link"] if entry["link"] else (not a["link"] and a["summary"] == entry["summary"])

        prior = next((a for a in state["actions"] if same(a)), None)
        if prior:
            entry["ts"] = prior["ts"]
        state["actions"] = [a for a in state["actions"] if not same(a)]
        state["actions"].append(entry)
        state["actions"].sort(key=lambda a: a["ts"])
        item = state["items"].setdefault(key, {"key": key})
        item["acted"] = {"ts": entry["ts"], "action": entry["action"], "link": entry["link"]}
    state["actions"] = state["actions"][-MAX_ACTIONS:]
    return len(actions)


def _merge_session(state: Dict[str, Any], data: Dict[str, Any]) -> int:
    state["session"] = {**state["session"], **data}
    return len(data)


_HANDLERS = {
    "items": _upsert_items,
    "views": _set_view,
    "actions": _log_actions,
    "session": _merge_session,
}


def update(section: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if section not in _HANDLERS:
        raise ValueError(f"section must be one of {SECTIONS}")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    with _locked():
        state = load()
        n = _HANDLERS[section](state, data)
        _save(state)
    return {"section": section, "written": n, "items": len(state["items"]), "actions": len(state["actions"])}
