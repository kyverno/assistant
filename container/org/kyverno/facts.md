# Kyverno: facts the assistant should know

Frozen at a research pass in 2026-10. Re-check `.github/workflows/`, `.claude/settings.json`, `api/AGENTS.md` and
`gh label list` if a decision depends on one of them. The structured rules (file classes, gate, labels) are in
`org.yaml`; this is the prose around them.

## Code generation

Two repositories split the API types. `kyverno.io`, `wgpolicyk8s.io` and `reports.kyverno.io` live in this repository's
`api/**`. The CEL-based `policies.kyverno.io` types (`ValidatingPolicy`, `MutatingPolicy`, and so on) live in the
separate `github.com/kyverno/api` Go module. A `go.mod` and `go.sum` bump of that module is the generated-input event
for that half: it looks like a Dependabot bump, not an `api/**` diff.

The generated paths are `file_classes.generated` in `org.yaml`, taken from `.claude/settings.json`. They have no `DO NOT
EDIT` header under `pkg/clients` (the path is the only signal, and `pkg/clients/dclient/client.go` is the one
hand-written exception). The CLI's copy of the CRDs is second-order: updating `config/crds/**` without it is itself a
generated-file conflict. Outside `pkg/clients`, the reliable "is this generated" signal is the
`// Code generated ... DO NOT EDIT.` header, not the filename. `check-codegen.yaml` gates all of this before merge: two
PRs feeding the same generated output conflict on it even when their own diffs don't overlap.

## CI before and after merge

Before merge (`pull_request`, gates the PR): `check-codegen`, `check-unit-tests`, `check-golangci-lint`, `check-vet`,
`check-imports`, `check-fmt`, `check-cli-tests`, `check-framework`, `check-ct-lint`, `check-ah-lint`,
`check-devcontainer`, `check-unused-package`, `check-sha-pinned-actions`.

After merge only (push to `main` or a release branch through `check-tests.yaml`, never on a PR): `tests-conformance.yaml`
(Chainsaw, 3 Kubernetes versions), `tests-conformance-policy-library.yaml` (12-way sharded, checks out the separate
`kyverno/policies` repository and is not scoped to any package here), `tests-k6.yaml`, and the performance benchmarks.
Nothing catches a bad interaction between two individually green PRs before both land; that is what the gate below
reacts to. A PR touching core policy-evaluation code (the `critical` file class) carries elevated post-merge risk on
this basis. There is no precise package-to-suite table, because the policy-library suite is not package-scoped.

## The e2e gate: branch-level, not path-level

When the post-merge conformance suite fails on a branch, an `e2e-failure` issue opens and the required "E2E Gate"
commit status turns red on every open PR targeting that branch, whichever files the PR touches. The only way out is the
PR carrying `e2e-gate-bypass`. Each issue's body names its branch as `<!-- workflow-failure:...:refs/heads/BRANCH -->`;
an issue with no marker blocks every branch. There is no mechanical "safe because it doesn't touch the failing files"
path. `ready-for-review` is independent of the gate: a PR can carry both.

## Labels (`.github/labels.yml`)

- `ready-for-review`: DCO, CI, conflicts and review threads are all clean. Human and Dependabot PRs share it.
- `needs-author-action`: a failing DCO or CI check, a merge conflict, or an unresolved thread on a human PR.
- `needs-review`: the Dependabot equivalent of `needs-author-action`: a major or unconfirmed-semver bump, failing CI, a
  conflict, or Copilot not recommending approval. Always name the actual cause, never just the label.
- `workflow-approval-required`: a fork PR waiting for a maintainer to approve its Actions run. It can co-occur with
  `needs-author-action`. No tool granted here can approve a run, so point at the GitHub UI.
- `milestone-pr`: closes an issue under a still-open `Kyverno Release ...` milestone. This is narrower than the PR having
  a milestone set; check both.
- `major-bump`: permanent, paired with `needs-review`.
- `stale` and `no-stale`: `stale` is a daily workflow's mark on a `needs-author-action` PR whose author has not pushed in
  14 days (it never closes anything; Dependabot and draft PRs are skipped). `no-stale` is a maintainer's exemption.
- `ai-generated` and `spam`: CodeRabbit's flags, not sequencing input.
- `release-critical`, `-high`, `-medium`, `-low`: not in `labels.yml`, and no workflow applies them. Maintainers set them
  by hand on an **issue** to mark urgency toward a milestone. Read a PR's closing issue's labels
  (`closing_issues[].labels`), not the PR's own.

## Milestones

A real field on the PR: `search_pull_requests(query='... milestone:"Kyverno Release X.Y.Z"')` filters on it directly,
for example `"Kyverno Release 1.20.0"`. `search_issues` takes natural language, so filter its results on the returned
`milestone` field instead.
