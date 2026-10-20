"""kyverno-dashboard plugin — update_dashboard tool + the Kyverno dashboard tab."""

from __future__ import annotations

from .tools import UPDATE_DASHBOARD_SCHEMA, handle_update_dashboard


def register(ctx) -> None:
    ctx.register_tool(
        name="update_dashboard",
        toolset="kyverno-dashboard",
        schema=UPDATE_DASHBOARD_SCHEMA,
        handler=handle_update_dashboard,
        description="Write queues, briefs, triage results and confirmed actions onto the "
        "maintainer's dashboard tab.",
        emoji="📊",
    )
