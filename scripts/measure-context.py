#!/usr/bin/env python3
"""Measure what each profile puts in the model's context before the first word.

Offline by default: plugin tool schemas, skill sizes, and the GitHub allowlist split into tools that a skill,
SOUL.md or cron prompt names and tools nothing names. --live also asks GitHub's hosted MCP server for the
schemas of the allowlisted tools (one read-only tools/list call with REPO_GITHUB_TOKEN). Token figures are
characters divided by four.

    python3 scripts/measure-context.py [--live] [--json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import sys
import urllib.request

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILES = ("maintainer", "contributor")
MCP_URL = "https://api.githubcopilot.com/mcp/"
sys.dont_write_bytecode = True


def tokens(chars: int) -> int:
    return round(chars / 4)


def load_plugin(name: str, directory: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, directory / "__init__.py", submodule_search_locations=[str(directory)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def plugin_schemas(profile: str) -> list[dict]:
    """The schemas each bundled plugin registers, captured through a stand-in for Hermes' register context."""
    schemas: list[dict] = []

    class Ctx:
        def register_tool(self, name, toolset, schema, handler, description="", emoji=""):
            schemas.append(schema)

    for directory in sorted((ROOT / "profiles" / profile / "plugins").iterdir()):
        if (directory / "__init__.py").exists():
            load_plugin(f"measure_{profile}_{directory.name.replace('-', '_')}", directory).register(Ctx())
    return schemas


def allowlist(profile: str) -> list[str]:
    config = yaml.safe_load((ROOT / "profiles" / profile / "config.yaml").read_text())
    return list(config["mcp_servers"]["github"]["tools"]["include"])


def named_text(profile: str) -> str:
    """Everything the model is told to use: skills, SOUL.md and cron prompts."""
    base = ROOT / "profiles" / profile
    parts = [p.read_text() for p in sorted(base.glob("skills/*/SKILL.md"))]
    if (base / "SOUL.md").exists():
        parts.append((base / "SOUL.md").read_text())
    jobs = base / "cron" / "jobs.json"
    if jobs.exists():
        parts += [j.get("prompt") or "" for j in json.loads(jobs.read_text()).get("jobs", [])]
    parts += [p.read_text() for p in sorted((ROOT / "container" / "skills").glob("*/SKILL.md"))]
    return "\n".join(parts)


def live_github_schemas(tools: list[str]) -> dict[str, int]:
    token = os.environ.get("REPO_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("--live needs REPO_GITHUB_TOKEN or GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream", "X-MCP-Tools": ",".join(tools)}

    def call(body: dict, session: str | None = None) -> tuple[dict, str | None]:
        h = dict(headers, **({"Mcp-Session-Id": session} if session else {}))
        req = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(), headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            sid = resp.headers.get("Mcp-Session-Id")
        payload = next((line[5:] for line in raw.splitlines() if line.startswith("data:")), raw)
        return (json.loads(payload) if payload.strip() else {}), sid

    _, session = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "measure-context", "version": "1"}}})
    call({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    result, _ = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
    return {t["name"]: len(json.dumps(t)) for t in result["result"]["tools"]}


def measure(profile: str, live: bool) -> dict:
    schemas = plugin_schemas(profile)
    text = named_text(profile)
    tools = allowlist(profile)
    unnamed = [t for t in tools if not re.search(rf"\b{re.escape(t)}\b", text)]
    skills = {p.parent.name: len(p.read_text()) for p in sorted((ROOT / "profiles" / profile).glob("skills/*/SKILL.md"))}
    out = {
        "profile": profile,
        "plugin_tools": {"count": len(schemas), "chars": sum(len(json.dumps(s)) for s in schemas)},
        "github_tools": {"count": len(tools), "unnamed": unnamed},
        "skills": {"count": len(skills), "chars": sum(skills.values()), "largest": max(skills.items(), key=lambda kv: kv[1]) if skills else None},
    }
    if live:
        sizes = live_github_schemas(tools)
        out["github_tools"]["chars"] = sum(sizes.values())
        out["github_tools"]["unnamed_chars"] = sum(sizes.get(t, 0) for t in unnamed)
        out["github_tools"]["missing_on_server"] = [t for t in tools if t not in sizes]
    return out


def report(m: dict) -> None:
    g, p, s = m["github_tools"], m["plugin_tools"], m["skills"]
    print(f"\n{m['profile']}")
    if "chars" in g:
        print(f"  GitHub tools     {g['count']:>3}  ~{tokens(g['chars']):>6,} tokens")
        print(f"    named by none  {len(g['unnamed']):>3}  ~{tokens(g['unnamed_chars']):>6,} tokens")
    else:
        print(f"  GitHub tools     {g['count']:>3}  (sizes need --live)")
        print(f"    named by none  {len(g['unnamed']):>3}")
    print(f"  plugin tools     {p['count']:>3}  ~{tokens(p['chars']):>6,} tokens")
    print(f"  skills (on demand) {s['count']:>1}  ~{tokens(s['chars']):>6,} tokens, largest {s['largest'][0]} ~{tokens(s['largest'][1]):,}")
    if g["unnamed"]:
        print("  unnamed GitHub tools: " + ", ".join(g["unnamed"]))
    if g.get("missing_on_server"):
        print("  allowlisted but not on the server: " + ", ".join(g["missing_on_server"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="measure GitHub tool schemas with one tools/list call")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()
    results = [measure(p, args.live) for p in PROFILES]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for m in results:
            report(m)
        print("\nMemory provider tools are not measured here; they load only inside the image.")


if __name__ == "__main__":
    main()
