"""Kyverno dashboard backend, mounted at /api/plugins/kyverno-dashboard/. Read-only: the agent
writes state through the update_dashboard tool; this just serves it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

# Loaded by file path: this module is imported standalone, so the plugin's relative imports
# aren't available here.
_spec = importlib.util.spec_from_file_location(
    "kyverno_dashboard_store", Path(__file__).resolve().parent.parent / "store.py"
)
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)


@router.get("/state")
def get_state():
    return store.load()
