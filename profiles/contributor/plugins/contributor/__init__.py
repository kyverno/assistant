"""Contributor plugin: cited facts about the repository, issues, PRs and the contributor's own journey."""

from __future__ import annotations

from .tools import TOOLS


def register(ctx) -> None:
    for schema, handler, emoji in TOOLS:
        ctx.register_tool(name=schema["name"], toolset="kyverno-contributor", schema=schema, handler=handler,
                          description=schema["description"].split(".")[0] + ".", emoji=emoji)
