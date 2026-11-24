"""The engine's tools over MCP, stdio transport: one JSON-RPC message per line on stdin and stdout.

Standard library only, so it runs under whichever Python the client starts it with. One process per client session,
so the session caches (patches by head commit, the pack, the "now" brief) live as long as the session. Calls run one
at a time, in order.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import traceback
from typing import Any, Optional

from . import TOOLS

NAME = "repo-intel"
VERSION = "0.2.0"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")

_BY_NAME = {schema["name"]: (schema, handler) for schema, handler in TOOLS}
_UNSET = re.compile(r"^\$\{[^}]+\}$")


def scrub_env(environ=os.environ) -> None:
    """An MCP client leaves `${VAR}` as written when VAR is unset; treat that as unset, not as a value."""
    for key in [k for k, v in environ.items() if _UNSET.match(v or "")]:
        del environ[key]


def tool_list() -> list[dict[str, Any]]:
    return [{"name": s["name"], "description": s["description"], "inputSchema": s["parameters"]}
            for s, _ in TOOLS]


def _call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if name not in _BY_NAME:
        raise _RpcError(-32602, f"unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise _RpcError(-32602, "arguments must be an object")
    try:
        # A handler or a library that prints would corrupt the protocol stream.
        with contextlib.redirect_stdout(sys.stderr):
            text = _BY_NAME[name][1](args)
    except Exception as exc:  # the model gets a short error; the trace goes to the client's log
        traceback.print_exc(file=sys.stderr)
        return {"content": [{"type": "text", "text": json.dumps(
            {"success": False, "error": f"{name} failed: {type(exc).__name__}: {exc}"})}], "isError": True}
    try:
        failed = json.loads(text).get("success") is False
    except (ValueError, AttributeError):
        failed = False
    return {"content": [{"type": "text", "text": text}], "isError": failed}


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def handle(message: Any) -> Optional[dict[str, Any]]:
    """One JSON-RPC message in, its response out (None for a notification)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32600, "message": "invalid request"}}
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params")
    params = {} if params is None else params
    try:
        if not isinstance(params, dict):
            raise _RpcError(-32602, "params must be an object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            result: Any = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                           "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": NAME, "version": VERSION}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_list()}
        elif method == "tools/call":
            result = _call(params)
        else:
            raise _RpcError(-32601, f"method not found: {method}")
    except _RpcError as exc:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": exc.code, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def _safe_handle(message: Any) -> Optional[dict[str, Any]]:
    """handle(), but a message it does not expect gets an internal error instead of stopping the server."""
    try:
        return handle(message)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        mid = message.get("id") if isinstance(message, dict) else None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "internal error"}}


def serve(stdin=None, stdout=None) -> None:
    if stdin is None:
        # MCP messages are UTF-8 whatever the locale the client passes (often none at all).
        for stream in (sys.stdin, sys.stdout):
            stream.reconfigure(encoding="utf-8", newline="\n")
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    scrub_env()
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply: Any = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if isinstance(message, list):  # a batch (protocol 2025-03-26)
                reply = [r for r in (_safe_handle(m) for m in message) if r is not None] or None
            else:
                reply = _safe_handle(message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
