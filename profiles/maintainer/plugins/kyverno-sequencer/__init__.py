"""kyverno-sequencer plugin — deterministic PR merge-sequencing tool for pr-queue."""

from __future__ import annotations

from .tools import SEQUENCE_PRS_SCHEMA, handle_sequence_prs


def register(ctx) -> None:
    ctx.register_tool(
        name="sequence_prs",
        toolset="kyverno-sequencer",
        schema=SEQUENCE_PRS_SCHEMA,
        handler=handle_sequence_prs,
        description="Deterministic candidate merge-sequence: file-risk graph, cycle "
        "detection, weighted CP-SAT rank solve.",
        emoji="🧮",
    )
