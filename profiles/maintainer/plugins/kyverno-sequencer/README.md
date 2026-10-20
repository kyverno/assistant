# kyverno-sequencer

A bundled Hermes plugin, not a skill. Registers one tool, `sequence_prs`, that `pr-queue`
calls with already-fetched PR metadata (from `kyverno-fetch`) and gets back a hard-
dependency graph layered into tiers — a graph builder, not a decision maker. Four hard-edge
types, all derived mechanically from the input (stacked branches, generated-file input-before-
output, an explicit "Depends on #N"-style body reference, a closing-issue conflict), cycle
detection, and a topological layering into tiers: within a tier, no hard dependency exists
between any two PRs, and ordering them is the agent's call — Slack context, mnemosyne
history, whatever the maintainer said they care about this session — not this tool's.

Ships automatically on `hermes profile install .` (see `docs/architecture.md`). No GitHub/
Slack access of its own — a pure function over its input, called once per queue-build. Pure
stdlib, no external dependency.
