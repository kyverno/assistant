# Making this adoptable by any organisation

Goal: an organisation that is not Kyverno can adopt this project with configuration only, and can take as much
or as little of it as it wants. Today the CI automation is being extracted in
[kyverno/kyverno#18275](https://github.com/kyverno/kyverno/pull/18275) and the assistants are Kyverno-specific.
This document fixes the shape so both converge.

## Three layers

| Layer | What it is | Needs a model? | Who adopts it |
| --- | --- | --- | --- |
| 1. CI actions (`actions/`) | Deterministic composite GitHub Actions: PR management, flaky tests, security triage, vulnerability scanning | No | Any org, from a workflow file |
| 2. Maintainer assistant (`profiles/maintainer`) | On-demand agent that orders a maintainer's queue and acts with their token | Yes | One maintainer, on their own machine |
| 3. Contributor assistant (`profiles/contributor`) | On-demand agent that helps a contributor get a PR merged | Yes | One contributor |

The layers are independent, but they share data: the actions label, classify and file issues in a way the
assistants then read. An assistant reads `needs-author-action`, `stale` and `ready-for-review` instead of
recomputing them, and a repo that runs layer 1 gets a better layer 2 for free. A repo without layer 1 still
works, but those labels are absent; whether the skills compute the same facts another way has not been checked.

The profile layout (`profiles/maintainer`, `profiles/contributor`) is already on `feat/profiles-contributor`.
This work does not touch it; it lands after that branch merges.

## One config model, three places

Everything org-specific is configuration. Nothing Kyverno-specific is allowed in code, only in config.

| Scope | File | Read by | Holds |
| --- | --- | --- | --- |
| A repo's CI behaviour | `.github/pr-management.json`, `.github/pr-readiness-checks.yml`, `.github/labels.yml` (and one config file per future action), in the **adopting repo** | the actions | reviewer team, exempt labels, timings, label catalog, required and ignored checks |
| An org's project knowledge | a **context pack** (below) | the assistants | labels in use, release and milestone conventions, codegen and CI facts, who owns what |
| One person's setup | `.env` / the launcher | the assistants | tokens, GitHub login, which repos |

Rules: the adopting repo owns its config and the action never reads config from a PR head in a write-capable
job (see the trust model in [`actions/pr-management/README.md`](../actions/pr-management/README.md)); an unknown
config key is an error, not ignored; every mutating mode defaults to dry run.

## Conventions for actions

1. **Location and reference.** `actions/<name>/action.yml`, used as `uses: <org>/assistant/actions/<name>@<full SHA>`.
   Adopters pin a reviewed commit; floating tags are not recommended.
2. **Composite, standard tools only.** Bash, Python 3 standard library, `jq`, `gh`. No npm or pip install at run
   time. Scripts load from `GITHUB_ACTION_PATH`, never from the consumer's checkout.
3. **Modes, not many actions.** One action per concern with a required `mode` input (`pr-management` has
   `labels`, `readiness`, `stale`, `sync-labels`, `validate`). Every action has an offline `validate` mode that
   needs no token and never executes PR-provided code, so config can be checked in ordinary `pull_request` CI.
4. **Dry run by default.** `dry-run` defaults to `'true'`. Adopters stage a mode in preview, read the logs,
   then set `'false'` per mode.
5. **Least privilege, documented per mode.** Each README has a table of the exact token permissions each mode
   needs. No action needs a cross-repository write credential.
6. **Conservative on missing data.** A failed lookup, a truncated page or a pending run never proves a good
   outcome and never causes a destructive one.
7. **Idempotent and marked.** Comments and issues carry a hidden marker so a re-run updates rather than
   duplicates.
8. **Configuration is data.** Anything a repo might want to change (names of checks, thresholds, labels,
   teams) is a config key; the code needs no edit to adopt it.

## Roadmap for the actions

Only `pr-management` exists. The others are possible later work, deliberately not started.

| Action | Sketch | Config keys (all repo-owned) | Output other layers use |
| --- | --- | --- | --- |
| `pr-management` | Done: ported as is from #18275; only a comment was changed | see its README | labels `needs-author-action`, `stale`, `ready-for-review` |
| `flaky-tests` | Read recent runs of named workflows through the Actions API, find tests that both pass and fail on the same commit or alternate across runs, open or update one tracking issue per test, optionally label the failing PR "known flaky" so authors are not blamed | workflows to watch, how test names are parsed (JUnit or `go test -json`), history window, failure-rate threshold, issue labels, quarantine label | `flaky` issues; the readiness `infra_checks` list can name known-flaky checks |
| `security-triage` | Collect code-scanning, secret-scanning and Dependabot alerts, group by severity and age, open or update a single tracking issue, ping the configured security team on new critical ones, flag alerts past SLA | severity SLAs in days, security team, alert sources enabled, exempt rules or paths, issue label | one issue per repo; severity labels |
| `vuln-scan` | Run a pinned scanner (Trivy) on the repo filesystem and named images, de-duplicate against open tracking issues, open one issue per distinct CVE with the fixed version when there is one | scan targets, severity floor, ignore list with expiry, whether to fail the job | issues labelled `vulnerability`; SARIF upload as an option |

`security-triage` reads alerts GitHub already produces; `vuln-scan` produces findings GitHub does not. Both
write issues in the same format so the maintainer assistant can read them without special cases. Order: first
`security-triage` (smallest, read-only inputs), then `flaky-tests`, then `vuln-scan` (largest, needs a pinned
scanner and image handling).

## Making the assistants generic

What is Kyverno-specific today, and where it goes:

| Today | Becomes |
| --- | --- |
| `KYVERNO_REPO` env var | `ASSISTANT_REPOS` (comma list of `owner/repo`); `KYVERNO_REPO` kept as an alias for one release |
| `skills/kyverno-context` (labels, codegen facts, `Kyverno Release X.Y.Z`) | a generic `repo-context` skill that does the live lookups, plus a **context pack** per org supplying the fixed facts |
| `plugins/kyverno-fetch`, `plugins/kyverno-sequencer` | renamed `repo-fetch`, `repo-sequencer`; file classes (`GENERATED`, `API_SURFACE`, `ADMISSION_CRITICAL`) and the "e2e gate" check name move from code to the context pack |
| `SOUL.md`, README, image and launcher names (`kyverno`, `kyverno-assistant`) | neutral names; Kyverno becomes the first context pack |
| hard-coded Kyverno hosts and paths in the sandbox policy | derived from `ASSISTANT_REPOS`; extra hosts listed in the pack |

A **context pack** is a directory (`contexts/<org>/`) with `context.yaml` (milestone naming, release branches,
check names to treat as infra, file-class globs, codegen directories), and optional `facts.md` for prose the
agent should know. `contexts/kyverno/` is built from what `kyverno-context` and the sequencer hold now; a new
org adds its own directory and points `ASSISTANT_CONTEXT` at it. The profile install copies it in the way it
copies skills.

The assistants stay human-invoked and still cannot merge; none of this changes the safety model.

## Order of work

1. **Done in this change:** `actions/pr-management` ported, `adopt-ci-templates/` caller workflows and config, `scripts/adopt-ci.sh`,
   `NOTICE` for the Apache-2.0 source, this document.
2. **Done:** `scripts/adopt-ci.sh`, one command that opens the adoption PR in each named repository; see
   [`adopting-actions.md`](adopting-actions.md).
3. Later, only if wanted: `security-triage`, `flaky-tests`, `vuln-scan`.
4. After `feat/profiles-contributor` merges: neutral names, `ASSISTANT_REPOS`, `repo-context` skill, plugin
   renames, and `contexts/kyverno/`.
5. Move Kyverno repos from #18275's copy to `kyverno/assistant/actions/pr-management@<sha>` once it is merged
   and this repo has a tagged, reviewed commit to pin.

## Open points

- Licensing: `actions/pr-management` is Apache-2.0 and the rest of the repo is MIT. `NOTICE` records it; confirm
  whether `actions/` should be Apache-2.0 as a whole.
- Whether the repo is renamed. `kyverno/assistant` is the pin path adopters would write, so the name should be
  settled before anyone pins it.
- No tests are committed here, by repo policy. #18275 ships a regression suite for the action; this port
  omits it. The scripts are copied from PR head `3030a2c`; only `validate` mode was run here, against the
  example config. `readiness` and `stale` were not exercised.
