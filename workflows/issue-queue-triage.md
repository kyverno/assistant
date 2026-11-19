---
description: |
  Sorts open issues that sit in no review queue into one queue each, adding a queue label and one short comment saying
  why. It never closes, edits or removes anything. A person still reviews every queue.

# This file is the repository's own configuration: `gh aw add` copies it, and you edit the `with:` block below. The
# behaviour lives in the shared component it imports. Pin that import to a full commit SHA of the assistant repository
# and move the pin on purpose, as with the other shared workflows.
on:
  schedule: daily

# copilot-requests lets the run use the built-in token for Copilot inference, billed to the organization, so no
# personal access token is stored. If the organization's Copilot billing does not allow it, drop this line and set a
# COPILOT_GITHUB_TOKEN secret instead.
permissions:
  contents: read
  issues: read
  copilot-requests: write

safe-outputs:
  # Preview first, like the issue sweep: while this is on, the run only reports the labels and comments it would add
  # and writes nothing to any issue. Read a few runs, then delete this line, run `gh aw compile` and commit to go live.
  staged: true

timeout-minutes: 20

imports:
  - uses: kyverno/assistant/workflows/shared/issue-queue-triage.md@main
    with:
      # Your five queue labels. They must exist: a label that does not exist is rejected. These are the defaults.
      critical_label: "triage:critical-review"
      repro_label: "triage:needs-repro"
      closure_label: "triage:verify-closure"
      scope_label: "triage:scope-review"
      decision_label: "triage:decision"
      # Issues with one of these labels are never sorted.
      exempt_labels: ["no-stale", "hold", "security"]
      # Files the agent reads once per run to learn who owns what (the ones that exist).
      context_files: ["CODEOWNERS", "ARCHITECTURE.md", "AGENTS.md", "CONTRIBUTING.md"]
      # The most issues one run sorts (the newest first).
      limit: 20
      # Anything specific to your repository that changes how issues are sorted.
      extra_rules: "None."
---

# Issue queue triage

The instructions for this workflow come from the imported shared component. Follow them.
